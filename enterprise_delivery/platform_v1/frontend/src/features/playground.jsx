import React, { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Play, Download, Copy } from "lucide-react";
import { request, queryOptions, control, downloadJson } from "../api";
import {
  Button,
  Field,
  PageTitle,
  Panel,
  ErrorBox,
  JsonView,
  Table,
  FilterBuilder,
  Badge,
} from "../components/ui";
export default function Playground() {
  const [target, setTarget] = useState("dataset");
  const [id, setId] = useState("");
  const [op, setOp] = useState("retrieve");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("auto");
  const [limit, setLimit] = useState(10);
  const [fields, setFields] = useState("");
  const [filter, setFilter] = useState(null);
  const [format, setFormat] = useState("parquet");
  const [exportJob, setExportJob] = useState(null);
  const datasets = useQuery(queryOptions(["visible-datasets"], "/v1/datasets"));
  const schema = useQuery(
    queryOptions(
      ["visible-schema", id],
      `/v1/datasets/${encodeURIComponent(id)}/schema`,
      Boolean(id) && target === "dataset",
    ),
  );
  const contract = useQuery(
    queryOptions(
      ["retrieval-contract", id],
      `/v1/datasets/${encodeURIComponent(id)}/retrieval-contract`,
      Boolean(id) && target === "dataset",
    ),
  );
  const path =
    target === "collection"
      ? `/v1/collections/${encodeURIComponent(id)}/retrieve`
      : `/v1/datasets/${encodeURIComponent(id)}/${op === "export" ? "exports" : op}`;
  const body =
    op === "query"
      ? {
          select: fields
            .split(",")
            .map((x) => x.trim())
            .filter(Boolean),
          filter,
          limit,
        }
      : op === "export"
        ? {
            select: fields
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean),
            filter,
            format,
            compression: format === "parquet" ? "zstd" : "none",
          }
        : { query, filter, mode, top_k: limit };
  const run = useMutation({
    mutationFn: (cursor) =>
      request(path, {
        method: "POST",
        body: cursor ? { ...body, cursor } : body,
      }),
    onSuccess: (d) => {
      if (op === "export") setExportJob(d.id);
    },
  });
  const job = useQuery({
    ...queryOptions(
      ["export", exportJob],
      `/v1/exports/${exportJob}`,
      !!exportJob,
    ),
    refetchInterval: (q) =>
      ["queued", "running"].includes(q.state.data?.status) ? 3000 : false,
  });
  const download = useMutation({
    mutationFn: () => request(`/v1/exports/${exportJob}/download`),
  });
  const cancel = useMutation({
    mutationFn: () => request(`/v1/exports/${exportJob}`, { method: "DELETE" }),
    onSuccess: () => job.refetch(),
  });
  const links = download.data
    ? [
        { label: "Manifest", url: download.data.manifest_url },
        ...(download.data.parts || []).map((p, i) => ({
          label: `Part ${i + 1}`,
          url: p.url,
        })),
        {
          label: "Download",
          url: download.data.download_url || download.data.url,
        },
      ].filter((x) => {
        try {
          return new URL(x.url).protocol === "https:";
        } catch {
          return false;
        }
      })
    : [];
  return (
    <>
      <PageTitle
        eyebrow="DEVELOPER TOOLS / PLAYGROUND"
        title="Explore with confidence"
        description="Every request uses your signed-in identity and effective permissions."
      />
      <div className="playground-grid">
        <Panel title="Request">
          <div className="panel-body">
            <div className="form-grid">
              <Field label="Target">
                <select
                  value={target}
                  onChange={(e) => {
                    setTarget(e.target.value);
                    setOp("retrieve");
                    setId("");
                    run.reset();
                  }}
                >
                  <option value="dataset">Dataset</option>
                  <option value="collection">Collection</option>
                </select>
              </Field>
              <Field label="Operation">
                <select
                  value={op}
                  onChange={(e) => {
                    setOp(e.target.value);
                    run.reset();
                    setExportJob(null);
                  }}
                >
                  {(target === "collection"
                    ? ["retrieve"]
                    : ["retrieve", "query", "export"]
                  ).map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </Field>
            </div>
            <Field
              label={target === "collection" ? "Collection ID" : "Dataset ID"}
            >
              <input
                list="datasets"
                value={id}
                onChange={(e) => {
                  setId(e.target.value);
                  run.reset();
                }}
              />
            </Field>
            <datalist id="datasets">
              {(datasets.data?.datasets || []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.display_name}
                </option>
              ))}
            </datalist>
            {op === "retrieve" ? (
              <>
                <Field label="Query">
                  <textarea
                    rows="4"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </Field>
                <Field label="Retrieval mode">
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    {["auto", "keyword", "vector", "hybrid"].map((x) => (
                      <option key={x}>{x}</option>
                    ))}
                  </select>
                </Field>
              </>
            ) : (
              <Field label="Select fields (comma separated)">
                <input
                  value={fields}
                  onChange={(e) => setFields(e.target.value)}
                  placeholder="Default authorized projection"
                />
              </Field>
            )}
            {op === "export" ? (
              <Field label="Format">
                <select
                  value={format}
                  onChange={(e) => setFormat(e.target.value)}
                >
                  {["parquet", "csv", "jsonl"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </Field>
            ) : (
              <Field label={op === "query" ? "Page size" : "Top K"}>
                <input
                  type="number"
                  min="1"
                  max={op === "query" ? 10000 : 1000}
                  value={limit}
                  onChange={(e) => setLimit(Number(e.target.value))}
                />
              </Field>
            )}
            <h3>Filters</h3>
            <FilterBuilder
              value={filter}
              onChange={setFilter}
              fields={schema.data?.fields || []}
            />
            <ErrorBox error={run.error} />
            <Button
              variant="primary"
              busy={run.isPending}
              disabled={!id || (op === "retrieve" && !query) || limit < 1}
              onClick={() => run.mutate(null)}
            >
              <Play size={16} />
              {op === "export" ? "Submit export" : "Run request"}
            </Button>
          </div>
        </Panel>
        <div>
          <Panel
            title="Response"
            action={
              run.data && (
                <Button onClick={() => downloadJson("response.json", run.data)}>
                  <Download size={14} />
                  Export
                </Button>
              )
            }
          >
            <div className="panel-body">
              {run.data ? (
                <>
                  <p className="muted">
                    Trace {run.data.trace_id || "Not reported"}
                  </p>
                  {run.data.results?.map((h, i) => (
                    <article
                      className="result"
                      key={`${h.source?.dataset}-${h.chunk_id}-${i}`}
                    >
                      <div className="actions">
                        <Badge value={`Rank ${i + 1}`} />
                        <strong>
                          {h.source?.document_name || h.record_id}
                        </strong>
                      </div>
                      <p>{h.text || "Text was not returned"}</p>
                      <details>
                        <summary>Citation & metadata</summary>
                        <JsonView
                          value={{
                            source: h.source,
                            metadata: h.metadata,
                            score: h.score,
                          }}
                        />
                      </details>
                    </article>
                  ))}
                  {run.data.rows && (
                    <Table
                      rows={run.data.rows}
                      columns={Object.keys(run.data.rows[0] || {}).map((k) => ({
                        key: k,
                        label: k,
                      }))}
                    />
                  )}
                  <Button
                    disabled={!run.data.next_cursor}
                    busy={run.isPending}
                    onClick={() => run.mutate(run.data.next_cursor)}
                  >
                    Next page
                  </Button>
                  <details>
                    <summary>Full response</summary>
                    <JsonView value={run.data} />
                  </details>
                </>
              ) : (
                <p>Run a request to see authorized results.</p>
              )}
              {job.data && (
                <>
                  <Badge value={job.data.status} />
                  <JsonView value={job.data} />
                  <Button
                    disabled={job.data.status !== "succeeded"}
                    busy={download.isPending}
                    onClick={() => download.mutate()}
                  >
                    Get download
                  </Button>
                </>
              )}
              {links.map((x) => (
                <p key={x.url}>
                  <a href={x.url} target="_blank" rel="noopener noreferrer">
                    {x.label}
                  </a>
                </p>
              ))}
              {["queued", "running"].includes(job.data?.status) && (
                <Button busy={cancel.isPending} onClick={() => cancel.mutate()}>
                  Cancel export
                </Button>
              )}
              <ErrorBox error={job.error || download.error || cancel.error} />
            </div>
          </Panel>
          <Panel title="Integration contract">
            <div className="panel-body">
              <details>
                <summary>Effective schema</summary>
                <ErrorBox error={schema.error} />
                <JsonView value={schema.data} />
              </details>
              <details>
                <summary>Retrieval contract</summary>
                <ErrorBox error={contract.error} />
                <JsonView value={contract.data} />
              </details>
              <details>
                <summary>Request body</summary>
                <code>POST {path}</code>
                <JsonView value={body} />
              </details>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}
