import { tool } from "@opencode-ai/plugin"

type JsonObject = Record<string, unknown>

type CompactPosition =
  | {
      game_id: string
      status: "active"
      side_to_move: string
      fen: string
      board: string[]
      legal_moves: Array<{ uci: string; san: string }>
    }
  | { game_id: string; status: "active"; waiting: "human_move" }
  | { result: string; termination: string }

const gameId = tool.schema.string().min(1).max(128)
const notation = tool.schema.string().min(1).max(32)
const explanation = tool.schema.string().trim().min(1).max(500)
const resignationReason = tool.schema.string().trim().min(1).max(240)
const square = tool.schema.string().regex(/^[a-h][1-8]$/)
const analysisLine = tool.schema.array(notation).max(3)
const ANALYSIS_CALL_LIMIT = 2
const analysisUsage = new Map<string, { fen: string; calls: number }>()
const imageUsage = new Map<string, string>()

function object(value: unknown): JsonObject | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : undefined
}

function string(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined
}

function cliError(stderr: string): Error {
  const parsed = parseJson(stderr)
  const message = object(parsed)?.error
  if (typeof message === "string" && message) return new Error(message)
  const lines = stderr
    .replace(/\x1B\[[0-?]*[ -/]*[@-~]/g, "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  return new Error(lines.at(-1)?.slice(0, 240) || "llmchess command failed")
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

async function runCli(args: string[], worktree: string): Promise<unknown> {
  const process = Bun.spawn(["uv", "run", "llmchess", ...args, "--json"], {
    cwd: worktree,
    stdout: "pipe",
    stderr: "pipe",
  })
  const [exitCode, stdout, stderr] = await Promise.all([
    process.exited,
    new Response(process.stdout).text(),
    new Response(process.stderr).text(),
  ])

  if (exitCode !== 0) throw cliError(stderr)

  const payload = parseJson(stdout)
  if (payload === undefined) throw new Error("llmchess returned invalid JSON")
  return payload
}

function terminal(state: JsonObject): { result: string; termination: string } | undefined {
  if (state.status !== "terminal") return undefined
  const result = string(state.result)
  const termination = string(state.termination)
  if (!result || !termination) throw new Error("llmchess returned an invalid terminal state")
  return { result, termination }
}

function compactPosition(payload: unknown): CompactPosition {
  const state = object(payload)
  if (!state) throw new Error("llmchess returned an invalid game state")

  const finished = terminal(state)
  if (finished) return finished

  const id = string(state.id)
  const status = string(state.status)
  const actor = string(state.expected_actor)
  if (!id || status !== "active" || !actor) {
    throw new Error("llmchess returned an invalid active state")
  }
  if (actor === "human") return { game_id: id, status: "active", waiting: "human_move" }
  if (actor !== "llm") throw new Error("llmchess returned an unknown expected actor")

  const sideToMove = string(state.turn)
  const fen = string(state.fen)
  const board = Array.isArray(state.board) ? state.board : undefined
  const moves = Array.isArray(state.legal_moves) ? state.legal_moves : undefined
  if (
    !sideToMove ||
    !fen ||
    !board ||
    board.length !== 8 ||
    !board.every((row) => typeof row === "string" && row.length === 8) ||
    !moves
  ) {
    throw new Error("llmchess returned an invalid LLM position")
  }

  const legalMoves = moves.map((move) => {
    const entry = object(move)
    const uci = string(entry?.uci)
    const san = string(entry?.san)
    if (!uci || !san) throw new Error("llmchess returned an invalid legal move")
    return { uci, san }
  })
  return { game_id: id, status: "active", side_to_move: sideToMove, fen, board, legal_moves: legalMoves }
}

function boundedAnalysis(payload: unknown): JsonObject {
  const state = object(payload)
  const id = string(state?.game_id)
  const baseFen = string(state?.base_fen)
  if (!state || !id || !baseFen) throw new Error("llmchess returned invalid analysis data")

  const usage = analysisUsage.get(id)
  const calls = usage?.fen === baseFen ? usage.calls : 0
  if (calls >= ANALYSIS_CALL_LIMIT) {
    throw new Error("analysis call limit reached for this turn")
  }
  const nextCalls = calls + 1
  analysisUsage.set(id, { fen: baseFen, calls: nextCalls })

  const { base_fen: _, ...result } = state
  return { ...result, analysis_calls_remaining: ANALYSIS_CALL_LIMIT - nextCalls }
}

function output(value: object): string {
  return JSON.stringify(value)
}

function imageResult(payload: unknown) {
  const state = object(payload)
  const id = string(state?.game_id)
  const fen = string(state?.fen)
  const perspective = string(state?.perspective)
  const mime = string(state?.mime)
  const png = string(state?.png_base64)
  if (!id || !fen || perspective !== "white" || mime !== "image/png" || !png?.startsWith("iVBOR")) {
    throw new Error("llmchess returned invalid board image data")
  }
  if (imageUsage.get(id) === fen) throw new Error("board image already viewed for this position")
  imageUsage.set(id, fen)
  return {
    title: `Board ${id}`,
    output: output({ game_id: id, fen, perspective }),
    attachments: [
      {
        type: "file" as const,
        mime,
        url: `data:${mime};base64,${png}`,
        filename: `${id.replace(/[^a-zA-Z0-9_-]/g, "_")}.png`,
      },
    ],
  }
}

const newGame = tool({
  description: "Create a chess game for a human color and return its compact current state.",
  args: { human: tool.schema.enum(["white", "black"]) },
  async execute(args, context) {
    return output(compactPosition(await runCli(["new", "--human", args.human], context.worktree)))
  },
})

export { newGame as new }

export const position = tool({
  description: "Get a game's compact current state. Legal moves are shown only when the LLM is to move.",
  args: { game_id: gameId },
  async execute(args, context) {
    return output(compactPosition(await runCli(["state", args.game_id], context.worktree)))
  },
})

export const board_image = tool({
  description:
    "View the current board as a 256x256 PNG from White's perspective. Use at most once per position and only with a model that supports image input.",
  args: { game_id: gameId },
  async execute(args, context) {
    return imageResult(await runCli(["image", args.game_id], context.worktree))
  },
})

export const human_move = tool({
  description: "Apply one human SAN or UCI move and return only the resulting compact state.",
  args: { game_id: gameId, move: notation },
  async execute(args, context) {
    const payload = object(
      await runCli(["move", args.game_id, args.move, "--actor", "human"], context.worktree),
    )
    if (!payload) throw new Error("llmchess returned an invalid move response")
    return output(compactPosition(payload.game))
  },
})

export const llm_move = tool({
  description: "Apply one legal LLM UCI move with its concise public explanation.",
  args: { game_id: gameId, move: notation, explanation },
  async execute(args, context) {
    const payload = object(
      await runCli(
        ["move", args.game_id, args.move, "--actor", "llm", "--explanation", args.explanation],
        context.worktree,
      ),
    )
    const applied = object(payload?.applied)
    const uci = string(applied?.uci)
    const san = string(applied?.san)
    const state = object(payload?.game)
    if (!uci || !san || !state) throw new Error("llmchess returned an invalid move response")

    const finished = terminal(state)
    if (finished) return output(finished)
    if (state.status !== "active") throw new Error("llmchess returned an invalid active state")
    const nextActor = string(state.expected_actor)
    if (nextActor !== "human" && nextActor !== "llm") {
      throw new Error("llmchess returned an unknown expected actor")
    }
    return output({ accepted: { uci, san }, next_actor: nextActor })
  },
})

export const resign = tool({
  description:
    "Resign the active game for the LLM when it concludes it cannot reasonably win or no longer wants to continue. This immediately awards the human a win.",
  args: { game_id: gameId, reason: resignationReason },
  async execute(args, context) {
    const state = object(
      await runCli(["resign", args.game_id, "--actor", "llm"], context.worktree),
    )
    const finished = state && terminal(state)
    if (!finished || finished.termination !== "resignation") {
      throw new Error("llmchess returned an invalid resignation state")
    }
    return output({ ...finished, reason: args.reason })
  },
})

export const try_line = tool({
  description:
    "Try a non-persistent legal line of one to three plies and see the resulting compact position. Shares a two-call budget per LLM turn with chess_piece_moves.",
  args: { game_id: gameId, moves: analysisLine.min(1) },
  async execute(args, context) {
    return output(
      boundedAnalysis(await runCli(["try-line", args.game_id, ...args.moves], context.worktree)),
    )
  },
})

export const piece_moves = tool({
  description:
    "See one piece's attacked squares and current legal moves, optionally after a non-persistent line of up to three plies. Shares a two-call budget per LLM turn with chess_try_line.",
  args: { game_id: gameId, square, line: analysisLine },
  async execute(args, context) {
    const cliArgs = ["piece-moves", args.game_id, args.square]
    if (args.line.length) cliArgs.push("--line", ...args.line)
    return output(boundedAnalysis(await runCli(cliArgs, context.worktree)))
  },
})
