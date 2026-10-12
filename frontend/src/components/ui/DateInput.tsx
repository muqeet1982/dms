import { forwardRef, useEffect, useRef, useState } from "react";

/**
 * A date written the way it is written here: DD/MM/YYYY in the box, YYYY-MM-DD in the state.
 *
 * A native `<input type="date">` is drawn by the browser in whatever locale the browser was installed with, and
 * the app cannot change it: on an en-US laptop sitting in Karachi it reads and expects MM/DD/YYYY, so a storeman
 * typing 03/04 for the fourth of March had April the third saved. This takes digits (the slashes type
 * themselves), still offers the same calendar behind the icon, and hands `onChange` the native `{target:{value}}`
 * shape carrying the ISO string every caller already stores — so it drops in where the native input was.
 */
export type DateInputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  value?: string | null;
  onChange?: (e: { target: { value: string } }) => void;
};

const pad = (n: number) => String(n).padStart(2, "0");

/** ISO (or anything Date accepts) → "DD/MM/YYYY", and "" for nothing. */
export function toDMY(v?: string | null): string {
  if (!v) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v));
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  const d = new Date(String(v));
  return Number.isNaN(d.getTime()) ? String(v) : `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
}

/** "DD/MM/YYYY" → ISO, or "" when the date does not exist. 31/02 is not a date and is refused, not rolled over. */
export function fromDMY(text: string): string {
  const digits = text.replace(/\D/g, "");
  if (digits.length !== 8 && digits.length !== 6) return "";
  const dd = Number(digits.slice(0, 2));
  const mm = Number(digits.slice(2, 4));
  // Two digits of year is what a hand writes on a carton; this century is the only sane reading of it.
  const yyyy = digits.length === 8 ? Number(digits.slice(4, 8)) : 2000 + Number(digits.slice(4, 6));
  if (!dd || !mm || mm > 12 || yyyy < 1900 || yyyy > 2999) return "";
  const d = new Date(Date.UTC(yyyy, mm - 1, dd));
  if (d.getUTCDate() !== dd || d.getUTCMonth() !== mm - 1) return ""; // 31 Feb and friends
  return `${yyyy}-${pad(mm)}-${pad(dd)}`;
}

/** Progressive mask: "0304" shows as "03/04" while it is still being typed. */
const mask = (raw: string) => {
  const d = raw.replace(/\D/g, "").slice(0, 8);
  if (d.length <= 2) return d;
  if (d.length <= 4) return `${d.slice(0, 2)}/${d.slice(2)}`;
  return `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}`;
};

export const DateInput = forwardRef<HTMLInputElement, DateInputProps>(function DateInput(
  { value, onChange, className = "", title, disabled, readOnly, min, max, placeholder, ...rest },
  ref,
) {
  const [text, setText] = useState(() => toDMY(value));
  const picker = useRef<HTMLInputElement>(null);
  // Follow the value when it is changed from outside (a form reset, a loaded record), but never overwrite what
  // is being typed: half a date parses to nothing, and re-rendering it would fight the person's keystrokes.
  useEffect(() => {
    const iso = fromDMY(text);
    if ((value || "") !== iso) setText(toDMY(value));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  const emit = (iso: string) => onChange?.({ target: { value: iso } });
  const small = /form-control-sm/.test(className);

  return (
    <div className={`input-group ${small ? "input-group-sm" : ""} flex-nowrap`}>
      <input
        {...rest}
        ref={ref}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        className={`form-control ${className}`}
        title={title ?? "Date — DD/MM/YYYY"}
        placeholder={placeholder || "dd/mm/yyyy"}
        disabled={disabled}
        readOnly={readOnly}
        value={text}
        onChange={(e) => {
          const next = mask(e.target.value);
          setText(next);
          const iso = fromDMY(next);
          // A complete date reaches the form at once; clearing the box clears the value. Anything in between is
          // still being typed and is left alone until blur, so a half-date never lands in the record.
          if (iso) emit(iso);
          else if (!next.replace(/\D/g, "")) emit("");
        }}
        onBlur={(e) => {
          const iso = fromDMY(text);
          if (iso) setText(toDMY(iso));
          else if (text.replace(/\D/g, "")) setText(toDMY(value)); // unparseable: put back what was there
          rest.onBlur?.(e);
        }}
      />
      {!readOnly && !disabled && (
        <>
          <button
            type="button"
            className="btn btn-outline-light border text-muted px-2"
            tabIndex={-1}
            title="Pick from a calendar"
            onClick={() => {
              const el = picker.current;
              if (!el) return;
              try {
                (el as any).showPicker ? (el as any).showPicker() : el.click();
              } catch {
                el.click();
              }
            }}
          >
            <i className="ti ti-calendar" />
          </button>
          {/* The calendar itself is still the browser's, which is the one thing it does well; only its text box
              was the problem, and this one has none the user can reach. */}
          <input
            ref={picker}
            type="date"
            tabIndex={-1}
            aria-hidden="true"
            min={min}
            max={max}
            value={fromDMY(text) || (value || "")}
            onChange={(e) => {
              setText(toDMY(e.target.value));
              emit(e.target.value);
            }}
            style={{ position: "absolute", width: 1, height: 1, opacity: 0, pointerEvents: "none", bottom: 0, right: 36 }}
          />
        </>
      )}
    </div>
  );
});

/**
 * A moment: the day written DD/MM/YYYY beside a clock. Emits what `datetime-local` emits —
 * `YYYY-MM-DDTHH:mm` — so the API sees no difference.
 *
 * The native `datetime-local` control has the same fault as the date one, in a field that records *when a
 * delivery happened*: an en-US device asks for MM/DD and the hour, and 03/04 at four is a different afternoon
 * from the one the driver meant.
 */
export function DateTimeInput({ value, onChange, className = "", disabled, readOnly }: {
  value?: string | null;
  onChange?: (e: { target: { value: string } }) => void;
  className?: string;
  disabled?: boolean;
  readOnly?: boolean;
}) {
  const [day = "", clock = ""] = String(value || "").split("T");
  const emit = (d: string, t: string) => onChange?.({ target: { value: d ? `${d}T${t || "00:00"}` : "" } });
  return (
    <div className="d-flex gap-2">
      <DateInput
        className={className}
        value={day}
        disabled={disabled}
        readOnly={readOnly}
        onChange={(e) => {
          // choosing a day with no time yet means "about now", which is what the field's own hint promises
          const now = new Date();
          emit(e.target.value, clock || (e.target.value ? `${pad(now.getHours())}:${pad(now.getMinutes())}` : ""));
        }}
      />
      <input
        type="time"
        className={`form-control ${className}`}
        style={{ maxWidth: 130 }}
        disabled={disabled}
        readOnly={readOnly}
        value={clock.slice(0, 5)}
        onChange={(e) => emit(day, e.target.value)}
      />
    </div>
  );
}
