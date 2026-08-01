/* Global filter bar — drives calendar, score, and analytics on Dashboard +
   Logbook + Analytics. Renders as the modern pill kit (components/filters). */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Edge, TagT, Trade } from "../api/types";
import { strategyLabel } from "../lib/format";
import { useAuth, useFilters, type RangePreset } from "../state/store";
import { FilterPill, FilterReset, FilterSeg, SearchPill } from "./filters";

const RANGES = [
  ["7D", "7D"],
  ["30D", "30D"],
  ["90D", "90D"],
  ["YTD", "YTD"],
  ["ALL", "All"],
] as const;

export function FilterBar() {
  const { filters, setFilters, active } = useFilters();
  const { accountEpoch } = useAuth();
  const [mechs, setMechs] = useState<Edge[]>([]);
  const [tags, setTags] = useState<TagT[]>([]);
  const [directions, setDirections] = useState<string[]>([]);

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const [m, t, tr] = await Promise.all([
          api<{ edges: Edge[] }>("/journal/edges"),
          api<{ tags: TagT[] }>("/journal/tags"),
          api<{ trades: Trade[] }>("/journal/trades"),
        ]);
        if (!dead) {
          setMechs(m.edges || []);
          setTags(t.tags || []);
          // distinct strategy shapes actually present — never the full catalog
          setDirections(
            [...new Set((tr.trades || []).map((x) => x.direction).filter(Boolean))].sort() as string[],
          );
        }
      } catch {
        /* filters degrade gracefully */
      }
    })();
    return () => {
      dead = true;
    };
  }, [accountEpoch]);

  return (
    <div className="fbar">
      <FilterSeg
        label="Date range"
        options={RANGES}
        value={filters.range}
        onChange={(r: RangePreset) => setFilters({ range: r })}
      />
      <FilterPill
        label="Edge"
        allLabel="All edges"
        value={filters.edgeId != null ? String(filters.edgeId) : null}
        options={mechs.map((m) => ({ value: String(m.id), label: m.name }))}
        onChange={(v) => setFilters({ edgeId: v ? Number(v) : null })}
      />
      <FilterPill
        label="Tag"
        allLabel="All tags"
        value={filters.tagId != null ? String(filters.tagId) : null}
        options={tags.map((t) => ({ value: String(t.id), label: t.name, swatch: t.color }))}
        onChange={(v) => setFilters({ tagId: v ? Number(v) : null })}
      />
      {directions.length > 0 && (
        <FilterPill
          label="Strategy"
          allLabel="All strategies"
          value={filters.direction || null}
          options={directions.map((d) => ({ value: d, label: strategyLabel(d) }))}
          onChange={(v) => setFilters({ direction: v ?? "" })}
        />
      )}
      <SearchPill
        placeholder="Underlying"
        value={filters.underlying}
        onChange={(v) => setFilters({ underlying: v })}
      />
      {active ? (
        <FilterReset
          onClear={() =>
            setFilters({ edgeId: null, tagId: null, underlying: "", direction: "", range: "ALL" })
          }
        />
      ) : null}
    </div>
  );
}
