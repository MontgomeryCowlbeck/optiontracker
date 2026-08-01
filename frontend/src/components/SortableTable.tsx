import { useMemo, useState, type ReactNode } from "react";
import { useNarrow } from "../lib/device";

export interface Column<T> {
  key: string;
  label: string;
  numeric?: boolean;
  sortable?: boolean;
  /** 1 = always shown, 2 = default, 3 = wide screens only (behind the
   *  "all columns" toggle on phones). Unset = 2. */
  priority?: number;
  value?: (row: T) => number | string | null | undefined; // sort value
  render: (row: T) => ReactNode;
}

export function SortableTable<T>({
  columns,
  rows,
  defaultSort,
  defaultDir = "desc",
  emptyText = "no closed trades",
  mini,
}: {
  columns: Column<T>[];
  rows: T[];
  defaultSort?: string;
  defaultDir?: "asc" | "desc";
  emptyText?: string;
  mini?: boolean;
}) {
  const [sortKey, setSortKey] = useState<string | null>(defaultSort ?? null);
  const [dir, setDir] = useState<"asc" | "desc">(defaultDir);
  const [allCols, setAllCols] = useState(false);
  const narrow = useNarrow(640);

  const hasWide = columns.some((c) => (c.priority ?? 2) >= 3);
  const visible = narrow && hasWide && !allCols
    ? columns.filter((c) => (c.priority ?? 2) <= 2)
    : columns;

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return rows;
    const val = (r: T) => {
      const v = col.value ? col.value(r) : (r as Record<string, unknown>)[col.key];
      return v == null ? null : (v as number | string);
    };
    return [...rows].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const cmp =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb));
      return dir === "asc" ? cmp : -cmp;
    });
  }, [rows, sortKey, dir, columns]);

  const onSort = (c: Column<T>) => {
    if (c.sortable === false) return;
    if (sortKey === c.key) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(c.key);
      setDir(c.numeric ? "desc" : "asc");
    }
  };

  return (
    <div>
      {narrow && hasWide && rows.length > 0 ? (
        <div className="row" style={{ justifyContent: "flex-end", padding: "6px 10px 0" }}>
          <button className="btn btn-ghost btn-sm" onClick={() => setAllCols(!allCols)}>
            {allCols ? "fewer columns" : "all columns"}
          </button>
        </div>
      ) : null}
      <div className="table-wrap">
        <table className={mini ? "mini-table" : undefined}>
          <thead>
            <tr>
              {visible.map((c) => (
                <th
                  key={c.key}
                  className={(c.numeric ? "n " : "") + (c.sortable === false ? "" : "sortable")}
                  aria-sort={
                    sortKey === c.key ? (dir === "asc" ? "ascending" : "descending") : undefined
                  }
                >
                  {c.sortable === false ? (
                    c.label
                  ) : (
                    <button type="button" className="th-sort" onClick={() => onSort(c)}>
                      {c.label}
                      {sortKey === c.key ? (
                        <span className="arrow">{dir === "asc" ? "▲" : "▼"}</span>
                      ) : null}
                    </button>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={visible.length} className="muted">
                  {emptyText}
                </td>
              </tr>
            ) : (
              sorted.map((r, i) => (
                <tr key={i}>
                  {visible.map((c) => (
                    <td key={c.key} className={c.numeric ? "n" : undefined}>
                      {c.render(r)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
