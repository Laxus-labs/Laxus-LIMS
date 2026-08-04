import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Sample, StorageLocation } from "../api/types";

interface TreeNode extends StorageLocation {
  children: TreeNode[];
}

function buildTree(locations: StorageLocation[]): TreeNode[] {
  const nodes = new Map<number, TreeNode>();
  locations.forEach((l) => nodes.set(l.id, { ...l, children: [] }));
  const roots: TreeNode[] = [];
  nodes.forEach((node) => {
    if (node.parent && nodes.has(node.parent)) {
      nodes.get(node.parent)!.children.push(node);
    } else {
      roots.push(node);
    }
  });
  return roots;
}

function LocationNode({ node, onSelect, selectedId }: { node: TreeNode; onSelect: (id: number) => void; selectedId: number | null }) {
  return (
    <li>
      <button
        className={`tree-node ${selectedId === node.id ? "selected" : ""}`}
        onClick={() => onSelect(node.id)}
      >
        {node.name} <span className="muted">({node.location_type})</span>
      </button>
      {node.children.length > 0 && (
        <ul>
          {node.children.map((child) => (
            <LocationNode key={child.id} node={child} onSelect={onSelect} selectedId={selectedId} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function LocationBrowser() {
  const [locations, setLocations] = useState<StorageLocation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [contents, setContents] = useState<Sample[]>([]);
  const [loadingContents, setLoadingContents] = useState(false);

  useEffect(() => {
    api.listLocations().then(setLocations);
  }, []);

  const tree = useMemo(() => buildTree(locations), [locations]);
  const selected = locations.find((l) => l.id === selectedId) ?? null;

  useEffect(() => {
    if (selectedId == null) return;
    setLoadingContents(true);
    api
      .locationContents(selectedId)
      .then(setContents)
      .finally(() => setLoadingContents(false));
  }, [selectedId]);

  return (
    <div>
      <h1>Storage</h1>
      <div className="grid-two">
        <section className="card">
          <h2>Hierarchy</h2>
          <ul className="tree">
            {tree.map((node) => (
              <LocationNode key={node.id} node={node} onSelect={setSelectedId} selectedId={selectedId} />
            ))}
          </ul>
        </section>

        <section className="card">
          <h2>{selected ? `Contents of ${selected.name}` : "Select a location"}</h2>
          {selected?.capacity && (
            <p className="muted">
              Grid: {selected.grid_rows}×{selected.grid_cols} ({selected.capacity} positions)
            </p>
          )}
          {loadingContents ? (
            <p>Loading…</p>
          ) : (
            <ul>
              {contents.map((s) => (
                <li key={s.id}>
                  <Link to={`/samples/${s.id}`}>{s.barcode}</Link> — {s.state}
                  {s.current_placement && s.current_placement.location.id === selectedId && (
                    <span className="muted">
                      {" "}
                      @ {s.current_placement.row_label}
                      {s.current_placement.column_number ?? ""}
                    </span>
                  )}
                </li>
              ))}
              {selected && contents.length === 0 && <li>Empty.</li>}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
