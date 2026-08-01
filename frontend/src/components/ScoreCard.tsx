/* Composite discipline/performance score — hero number + six labeled bars.
   (Bars over radar per dataviz guidance: magnitude on a common 0-100 scale.) */
import type { Score } from "../api/types";
import { money } from "../lib/format";
import { Empty } from "./ui";

const LABELS: [string, string][] = [
  ["win_rate", "Win rate"],
  ["payoff", "Payoff"],
  ["profit_factor", "Profit factor"],
  ["drawdown", "Drawdown"],
  ["consistency", "Consistency"],
  ["recovery", "Recovery"],
];

export function ScoreCard({ score }: { score: Score }) {
  if (!score.trades) {
    return (
      <Empty hint="Close some journaled trades and the score builds itself.">
        No closed trades in this slice yet.
      </Empty>
    );
  }
  return (
    <div>
      <div className="score-hero">
        <span className="big">{Math.round(score.score)}</span>
        <span className="muted small">
          / 100 · {score.trades} closed trade{score.trades === 1 ? "" : "s"}
          {score.max_drawdown != null ? ` · max drawdown ${money(-Math.abs(score.max_drawdown))}` : ""}
        </span>
      </div>
      <div className="score-bars">
        {LABELS.map(([key, label]) => {
          const v = Math.max(0, Math.min(100, score.components[key] ?? 0));
          return (
            <div key={key} className="score-row">
              <span className="muted">{label}</span>
              <div className="bar">
                <div className="fill" style={{ width: `${v}%` }} />
              </div>
              <span className="v">{Math.round(v)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
