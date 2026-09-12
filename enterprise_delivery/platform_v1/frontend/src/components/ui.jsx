import React, { useEffect, useId, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Circle,
  X,
  RefreshCw,
  Plus,
  Trash2,
} from "lucide-react";
export function Button({ children, variant = "", busy = false, ...props }) {
  return (
    <button
      className={`button ${variant}`}
      {...props}
      disabled={props.disabled || busy}
    >
      {busy && <RefreshCw className="spin" size={16} />}
      {children}
    </button>
  );
}
export function Badge({ value }) {
  const s = String(value ?? "unknown");
  const good = [
    "active",
    "healthy",
    "succeeded",
    "allowed",
    "true",
    "valid",
  ].includes(s);
  const bad = ["failed", "disabled", "denied", "critical", "invalid"].includes(
    s,
  );
  const Icon = good ? CheckCircle2 : bad ? AlertCircle : Circle;
  return (
    <span className={`badge ${good ? "good" : bad ? "bad" : "neutral"}`}>
      <Icon size={12} />
      {s.replaceAll("_", " ")}
    </span>
  );
}
export function ErrorBox({ error, retry }) {
  if (!error) return null;
  return (
    <div className="error-box" role="alert">
      <AlertCircle size={20} />
      <div>
        <strong>
          {error.status === 409
            ? "This record changed. Reload before saving."
            : error.message || String(error)}
        </strong>
        {error.trace && <small>Trace: {error.trace}</small>}
        {retry && (
          <Button onClick={retry}>
            <RefreshCw size={14} />
            Retry
          </Button>
        )}
      </div>
    </div>
  );
}
export function Empty({ title = "Nothing here yet", children, action }) {
  return (
    <div className="empty">
      <Circle size={30} />
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status" aria-label="Loading">
      <div />
      <div />
      <div />
    </div>
  );
}
export function PageTitle({ eyebrow, title, description, children }) {
  return (
    <div className="page-title">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="actions">{children}</div>
    </div>
  );
}
export function Panel({ title, children, action, className = "" }) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <header className="panel-heading">
          <h2>{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
export function JsonView({ value }) {
  return <pre className="code">{JSON.stringify(value, null, 2)}</pre>;
}
export function Field({ label, hint, children }) {
  const generated = useId();
  const id = children?.props?.id || generated;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {React.isValidElement(children)
        ? React.cloneElement(children, {
            id,
            "aria-describedby": hint ? id + "-hint" : undefined,
          })
        : children}
      {hint && <small id={id + "-hint"}>{hint}</small>}
    </div>
  );
}
export function Modal({ title, onClose, children, wide = false }) {
  const ref = useRef();
  const id = useId();
  useEffect(() => {
    const el = ref.current;
    el.showModal();
    return () => el.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? "wide" : ""}`}
      aria-labelledby={id}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <header>
        <h2 id={id}>{title}</h2>
        <Button aria-label="Close dialog" onClick={onClose}>
          <X size={18} />
        </Button>
      </header>
      {children}
    </dialog>
  );
}
export function Table({ columns, rows, idKey = "id", onOpen }) {
  if (!rows.length)
    return (
      <Empty title="No matching records">
        Adjust the filters or add a record.
      </Empty>
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row[idKey] || i}>
              {columns.map((c, j) => (
                <td key={c.key}>
                  {c.render ? (
                    c.render(row[c.key], row)
                  ) : j === 0 && onOpen ? (
                    <button className="text-button" onClick={() => onOpen(row)}>
                      {String(row[c.key] ?? row.id ?? "Open")}
                    </button>
                  ) : (
                    display(row[c.key])
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function display(v) {
  if (v == null) return <span className="muted">Unavailable</span>;
  if (typeof v === "boolean") return <Badge value={v} />;
  if (Array.isArray(v)) return v.join(", ") || "None";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
export function FilterBuilder({ value, onChange, fields = [] }) {
  const id = useId();
  function leaf() {
    return {
      field: fields.find((f) => f.filterable)?.name || fields[0]?.name || "",
      op: "eq",
      value: "",
    };
  }
  function node(expr, change, depth = 0) {
    if (depth > 10) return <JsonView value={expr} />;
    const logic = expr?.and
      ? "and"
      : expr?.or
        ? "or"
        : expr?.not
          ? "not"
          : null;
    if (logic) {
      const children = logic === "not" ? [expr.not] : expr[logic];
      return (
        <div className="filter-group">
          <div className="actions">
            <select
              aria-label="Filter combination"
              value={logic}
              onChange={(e) =>
                change(
                  e.target.value === "not"
                    ? {
                        not:
                          children.length === 1
                            ? children[0]
                            : { and: children },
                      }
                    : { [e.target.value]: children },
                )
              }
            >
              <option value="and">Match all conditions</option>
              <option value="or">Match any condition</option>
              <option value="not">Exclude matching rows</option>
            </select>
            {logic !== "not" && (
              <>
                <Button
                  type="button"
                  onClick={() => change({ [logic]: [...children, leaf()] })}
                >
                  <Plus size={14} />
                  Condition
                </Button>
                <Button
                  type="button"
                  onClick={() =>
                    change({ [logic]: [...children, { and: [leaf()] }] })
                  }
                >
                  Group
                </Button>
              </>
            )}
          </div>
          {children.map((child, i) => (
            <div className="filter-child" key={i}>
              {node(
                child,
                (v) => {
                  if (v === null) {
                    const next = children.filter((_, j) => j !== i);
                    change(
                      next.length
                        ? logic === "not"
                          ? { not: next[0] }
                          : { [logic]: next }
                        : null,
                    );
                  } else
                    change(
                      logic === "not"
                        ? { not: v }
                        : {
                            [logic]: children.map((c, j) => (i === j ? v : c)),
                          },
                    );
                },
                depth + 1,
              )}
            </div>
          ))}
          <Button
            type="button"
            aria-label="Remove group"
            onClick={() => change(null)}
          >
            <Trash2 size={14} />
            Remove group
          </Button>
        </div>
      );
    }
    const current = expr || leaf();
    const update = (patch) => change({ ...current, ...patch });
    const binding =
      current.value &&
      typeof current.value === "object" &&
      !Array.isArray(current.value) &&
      "$principal" in current.value;
    const field = fields.find((f) => f.name === current.field);
    const type = field?.data_type?.toLowerCase() || "text";
    function parse(text) {
      if (binding) return;
      let v = text;
      const arrays = [
        "in",
        "not_in",
        "between",
        "array_overlaps",
        "array_contains_all",
      ];
      if (arrays.includes(current.op) || current.op === "json_contains") {
        try {
          v = JSON.parse(text);
        } catch {
          v = text;
        }
      } else if (
        current.op === "exists" ||
        current.op === "array_is_empty" ||
        type === "boolean" ||
        type === "bool"
      )
        v = text === "true";
      else if (
        /^(int|bigint|smallint|serial|float|numeric|decimal|double|real)/.test(
          type,
        ) &&
        text !== ""
      )
        v = Number(text);
      update({ value: v });
    }
    return (
      <div className="filter-leaf">
        <div className="filter-row">
          <input
            aria-label="Filter field"
            list={id}
            value={current.field || ""}
            onChange={(e) => update({ field: e.target.value })}
          />
          <select
            aria-label="Filter operator"
            value={current.op}
            onChange={(e) => update({ op: e.target.value })}
          >
            {[
              "eq",
              "neq",
              "in",
              "not_in",
              "exists",
              "contains",
              "starts_with",
              "ends_with",
              "gt",
              "gte",
              "lt",
              "lte",
              "between",
              "array_contains_all",
              "array_overlaps",
              "array_is_empty",
              "json_contains",
            ].map((o) => (
              <option key={o}>{o}</option>
            ))}
          </select>
          <Button
            type="button"
            aria-label="Remove condition"
            onClick={() => change(null)}
          >
            <Trash2 size={14} />
          </Button>
        </div>
        <div className="filter-row">
          <select
            aria-label="Value source"
            value={binding ? "principal" : "literal"}
            onChange={(e) =>
              update({
                value:
                  e.target.value === "principal"
                    ? { $principal: "subject" }
                    : "",
              })
            }
          >
            <option value="literal">Literal value</option>
            <option value="principal">Principal attribute</option>
          </select>
          {binding ? (
            <select
              aria-label="Principal attribute"
              value={current.value.$principal}
              onChange={(e) =>
                update({ value: { $principal: e.target.value } })
              }
            >
              {["subject", "tenant", "client_id", "agent_id", "groups"].map(
                (x) => (
                  <option key={x}>{x}</option>
                ),
              )}
            </select>
          ) : (
            <input
              aria-label="Filter value"
              value={
                typeof current.value === "object"
                  ? JSON.stringify(current.value)
                  : String(current.value ?? "")
              }
              placeholder={
                [
                  "in",
                  "between",
                  "not_in",
                  "array_overlaps",
                  "array_contains_all",
                  "json_contains",
                ].includes(current.op)
                  ? "JSON value, e.g. [1,2]"
                  : "Value"
              }
              onChange={(e) => parse(e.target.value)}
            />
          )}
        </div>
        {current.path && (
          <Field label="JSON path (comma separated)">
            <input
              value={current.path.join(",")}
              onChange={(e) =>
                update({ path: e.target.value.split(",").map((x) => x.trim()) })
              }
            />
          </Field>
        )}
      </div>
    );
  }
  return (
    <div className="filter-builder">
      {value ? (
        node(value, onChange)
      ) : (
        <Button type="button" onClick={() => onChange({ and: [leaf()] })}>
          <Plus size={14} />
          Add condition
        </Button>
      )}
      <datalist id={id}>
        {fields.map((f) => (
          <option key={f.name} value={f.name} />
        ))}
      </datalist>
    </div>
  );
}
export function SchemaFields({
  schema,
  root = schema,
  value = {},
  onChange,
  path = "",
  locked = [],
  fields = [],
}) {
  if (schema.$ref) schema = root.$defs[schema.$ref.split("/").pop()];
  if (schema.anyOf) {
    const choices = schema.anyOf.filter((x) => x.type !== "null");
    schema = choices[0];
    if (schema.$ref) schema = root.$defs[schema.$ref.split("/").pop()];
  }
  const props = schema.properties || {};
  return (
    <div className="schema-fields">
      {Object.entries(props).map(([name, def]) => {
        if (name === "revision") return null;
        const p = path ? `${path}.${name}` : name;
        return (
          <SchemaInput
            key={p}
            name={name}
            schema={def}
            root={root}
            value={value?.[name]}
            onChange={(v) => onChange({ ...value, [name]: v })}
            required={schema.required?.includes(name)}
            locked={locked.includes(p)}
            fields={fields}
          />
        );
      })}
    </div>
  );
}
function SchemaInput({
  name,
  schema,
  root,
  value,
  onChange,
  required,
  locked,
  fields,
}) {
  let s = schema;
  if (s.$ref) s = root.$defs[s.$ref.split("/").pop()];
  let nullable = false;
  if (s.anyOf) {
    nullable = s.anyOf.some((x) => x.type === "null");
    s = s.anyOf.find((x) => x.type !== "null") || s;
    if (s.$ref) s = root.$defs[s.$ref.split("/").pop()];
  }
  const label = name.replaceAll("_", " ");
  const id = useId();
  if (name === "mandatory_filter")
    return (
      <fieldset>
        <legend>Row filter</legend>
        <FilterBuilder value={value} onChange={onChange} fields={fields} />
      </fieldset>
    );
  if (s.type === "boolean")
    return (
      <label className="check">
        <input
          type="checkbox"
          checked={value ?? s.default ?? false}
          disabled={locked}
          onChange={(e) => onChange(e.target.checked)}
        />
        {label}
      </label>
    );
  if (s.enum)
    return (
      <Field label={label}>
        <select
          id={id}
          value={value ?? s.default ?? ""}
          required={required}
          disabled={locked}
          onChange={(e) => onChange(e.target.value || null)}
        >
          {(!required || value == null) && <option value="">Select…</option>}
          {s.enum.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
      </Field>
    );
  if (s.type === "object" && s.properties)
    return (
      <fieldset>
        <legend>{label}</legend>
        {nullable && value == null ? (
          <Button type="button" onClick={() => onChange(defaults(s, root))}>
            <Plus size={14} />
            Configure {label}
          </Button>
        ) : (
          <>
            <SchemaFields
              schema={s}
              root={root}
              value={value || {}}
              onChange={onChange}
              fields={fields}
            />
            {nullable && (
              <Button type="button" onClick={() => onChange(null)}>
                Remove {label}
              </Button>
            )}
          </>
        )}
      </fieldset>
    );
  if (s.type === "array") {
    let item = s.items;
    if (item?.$ref) item = root.$defs[item.$ref.split("/").pop()];
    if (item?.enum)
      return (
        <fieldset>
          <legend>{label}</legend>
          <div className="checks">
            {item.enum.map((x) => (
              <label className="check" key={x}>
                <input
                  type="checkbox"
                  checked={(value || []).includes(x)}
                  onChange={(e) =>
                    onChange(
                      e.target.checked
                        ? [...(value || []), x]
                        : (value || []).filter((v) => v !== x),
                    )
                  }
                />
                {x}
              </label>
            ))}
          </div>
        </fieldset>
      );
    if (item?.type === "object")
      return (
        <fieldset>
          <legend>{label}</legend>
          {(value || []).map((row, i) => (
            <div className="array-item" key={i}>
              <SchemaFields
                schema={item}
                root={root}
                value={row}
                onChange={(v) =>
                  onChange(value.map((r, j) => (i === j ? v : r)))
                }
              />
              <Button
                type="button"
                onClick={() => onChange(value.filter((_, j) => i !== j))}
              >
                Remove entry
              </Button>
            </div>
          ))}
          <Button
            type="button"
            onClick={() => onChange([...(value || []), defaults(item, root)])}
          >
            <Plus size={14} />
            Add entry
          </Button>
        </fieldset>
      );
    return <ListInput label={label} value={value} onChange={onChange} />;
  }
  if (s.type === "object")
    return <MapInput label={label} value={value} onChange={onChange} />;
  return (
    <Field label={`${label}${required ? " *" : ""}`} hint={s.description}>
      <input
        id={id}
        type={s.type === "integer" || s.type === "number" ? "number" : "text"}
        value={value ?? s.default ?? ""}
        required={required}
        readOnly={locked}
        min={s.minimum}
        max={s.maximum}
        minLength={s.minLength}
        maxLength={s.maxLength}
        pattern={s.pattern}
        onChange={(e) =>
          onChange(
            e.target.value === ""
              ? nullable
                ? null
                : ""
              : s.type === "integer" || s.type === "number"
                ? Number(e.target.value)
                : e.target.value,
          )
        }
      />
    </Field>
  );
}
function ListInput({ label, value, onChange }) {
  const [draft, setDraft] = useState((value || []).join(", "));
  return (
    <Field label={label} hint="Separate values with commas">
      <input
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          onChange(
            e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          );
        }}
      />
    </Field>
  );
}
function MapInput({ label, value, onChange }) {
  const [text, setText] = useState(JSON.stringify(value || {}, null, 2));
  return (
    <Field label={label} hint="Advanced mapping · JSON object">
      <textarea
        value={text}
        rows={3}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const x = JSON.parse(e.target.value);
            if (!x || Array.isArray(x) || typeof x !== "object") throw Error();
            e.target.setCustomValidity("");
            onChange(x);
          } catch {
            e.target.setCustomValidity("Enter a valid JSON object");
          }
        }}
      />
    </Field>
  );
}
export function defaults(schema, root = schema) {
  if (schema.$ref) schema = root.$defs[schema.$ref.split("/").pop()];
  return Object.fromEntries(
    Object.entries(schema.properties || {})
      .filter(([k]) => k !== "revision")
      .map(([k, s]) => [
        k,
        s.default !== undefined
          ? s.default
          : s.type === "array"
            ? []
            : s.type === "object"
              ? {}
              : s.type === "boolean"
                ? false
                : "",
      ]),
  );
}
