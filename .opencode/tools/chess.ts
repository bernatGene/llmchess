import { tool } from "@opencode-ai/plugin"

type JsonObject = Record<string, unknown>

type CompactPosition =
  | {
      game_id: string
      status: "active"
      side_to_move: string
      fen: string
      legal_moves: Array<{ uci: string; san: string }>
    }
  | { game_id: string; status: "active"; waiting: "human_move" }
  | { result: string; termination: string }

const gameId = tool.schema.string().min(1).max(128)
const notation = tool.schema.string().min(1).max(32)
const explanation = tool.schema.string().trim().min(1).max(500)

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
  const moves = Array.isArray(state.legal_moves) ? state.legal_moves : undefined
  if (!sideToMove || !fen || !moves) throw new Error("llmchess returned an invalid LLM position")

  const legalMoves = moves.map((move) => {
    const entry = object(move)
    const uci = string(entry?.uci)
    const san = string(entry?.san)
    if (!uci || !san) throw new Error("llmchess returned an invalid legal move")
    return { uci, san }
  })
  return { game_id: id, status: "active", side_to_move: sideToMove, fen, legal_moves: legalMoves }
}

function output(value: object): string {
  return JSON.stringify(value)
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
