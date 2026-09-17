import React, { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { ArrowLeft, Database, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { request, control, queryOptions } from "../api";
import {
  Button,
  Field,
  PageTitle,
  Panel,
  ErrorBox,
  Loading,
  Table,
} from "../components/ui";
import { SourceObjects, ResourceEditor } from "./resources";
export default function Onboarding() {
  const [source, setSource] = useState(null);
  const [object, setObject] = useState(null);
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("table");
  const sources = useQuery(
    queryOptions(["wizard-sources"], `${control}/sources?limit=200`),
  );
  const forms = useQuery(queryOptions(["forms"], `${control}/forms`));
  const preview = useMutation({
    mutationFn: () =>
      request(`${control}/onboarding/preview`, {
        method: "POST",
        body: {
          source_id: source.id,
          schema_name: object.schema_name,
          object_name: object.object_name,
          dataset_id: id,
          display_name: name,
          template,
          environment: source.environment || "prod",
        },
      }),
  });
  if (preview.data && forms.data)
    return (
      <ResourceEditor
        type="datasets"
        initial={preview.data.dataset}
        schema={forms.data.datasets}
        creating
      />
    );
  return (
    <>
      <Link className="back-link" to="/datasets">
        <ArrowLeft size={15} />
        Data products
      </Link>
      <PageTitle
        eyebrow="DATA PRODUCTS / ONBOARDING"
        title="Bring your data into focus"
        description="Inspect the source. Define the contract. Validate before activation."
      />
      <div className="steps">
        <span className={!source ? "current" : ""}>1 · Source</span>
        <span className={source && !object ? "current" : ""}>2 · Object</span>
        <span className={object ? "current" : ""}>3 · Contract</span>
        <span>4 · Validate & activate</span>
      </div>
      <Panel
        title={
          !source
            ? "Choose a source"
            : !object
              ? "Choose an object"
              : "Name the data product"
        }
      >
        <div className="panel-body">
          {!source ? (
            sources.isPending ? (
              <Loading />
            ) : sources.isError ? (
              <ErrorBox error={sources.error} retry={sources.refetch} />
            ) : (
              <Table
                rows={sources.data.sources}
                columns={[
                  {
                    key: "id",
                    label: "Source",
                    render: (v, r) => (
                      <button
                        className="text-button"
                        onClick={() => setSource(r)}
                      >
                        <Database size={16} />
                        {v}
                      </button>
                    ),
                  },
                  { key: "kind", label: "Connector" },
                  { key: "environment", label: "Environment" },
                ]}
              />
            )
          ) : !object ? (
            <SourceObjects sourceId={source.id} onSelect={setObject} />
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                preview.mutate();
              }}
            >
              <div className="form-grid">
                <Field label="Dataset ID">
                  <input
                    required
                    pattern="[a-z0-9][a-z0-9._-]{1,127}"
                    value={id}
                    onChange={(e) => setId(e.target.value)}
                    placeholder="rdh.rimdocs.clinical"
                  />
                </Field>
                <Field label="Display name">
                  <input
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="RimDocs clinical"
                  />
                </Field>
                <Field label="Template">
                  <select
                    value={template}
                    onChange={(e) => setTemplate(e.target.value)}
                  >
                    <option value="table">Structured table</option>
                    <option value="postgres_chunks">
                      PostgreSQL document chunks
                    </option>
                  </select>
                </Field>
                <Field label="Source object">
                  <input
                    readOnly
                    value={`${object.schema_name || ""}.${object.object_name}`}
                  />
                </Field>
              </div>
              <ErrorBox error={preview.error} />
              <Button variant="primary" busy={preview.isPending}>
                Inspect & draft contract
                <ArrowRight size={16} />
              </Button>
            </form>
          )}
          {source && (
            <Button
              onClick={() => (object ? setObject(null) : setSource(null))}
            >
              <ArrowLeft size={14} />
              Back
            </Button>
          )}
        </div>
      </Panel>
    </>
  );
}
