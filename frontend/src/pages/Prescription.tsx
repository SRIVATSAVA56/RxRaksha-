/* ───────────────────────────────────────────────────────
   Prescription.tsx — /review/prescription screen
   Upload or type a prescription → normalize → navigate to /review/confirm
   NEVER runs medication analysis (that's after Confirm).
   ─────────────────────────────────────────────────────── */

import { useState, useRef, useCallback, useEffect, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  Image,
  ClipboardList,
  Upload,
  X,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { extractPrescription, normalizeMedicines, ApiRequestError } from "../api";
import { useAppDispatch } from "../state";

/* ── Constants ── */
const MAX_SIZE_MB = 10;
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024;
const ACCEPTED_TYPES = ["image/jpeg", "image/png"];

/* JPEG starts with FF D8 FF, PNG with 89 50 4E 47 */
async function validateMagicBytes(file: File): Promise<boolean> {
  const buf = await file.slice(0, 4).arrayBuffer();
  const bytes = new Uint8Array(buf);
  const isJpeg = bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  const isPng =
    bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
  return isJpeg || isPng;
}

/* ── Typed-row local state ── */
interface TypedRow {
  id: number;
  name: string;
  dose: string;
  frequency: string;
  doseNotSpecified: boolean;
  freqNotSpecified: boolean;
}

let nextRowCounter = 1;
function makeRow(): TypedRow {
  return {
    id: nextRowCounter++,
    name: "",
    dose: "",
    frequency: "",
    doseNotSpecified: false,
    freqNotSpecified: false,
  };
}

/* ═══════════════════════════════════════════════════════
   Component
   ═══════════════════════════════════════════════════════ */

export default function Prescription() {
  const navigate = useNavigate();
  const dispatch = useAppDispatch();

  /* ── Tabs ── */
  const [activeTab, setActiveTab] = useState<"upload" | "type">("upload");

  /* ── Upload state ── */
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadApiError, setUploadApiError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  /* ── Type state ── */
  const [rows, setRows] = useState<TypedRow[]>([makeRow()]);
  const [typing, setTyping] = useState(false);
  const [typeApiError, setTypeApiError] = useState<string | null>(null);

  /* ── Tab keyboard nav (arrow keys + Enter) ── */
  const handleTabKeyDown = useCallback(
    (e: KeyboardEvent, tab: "upload" | "type") => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        setActiveTab(tab === "upload" ? "type" : "upload");
      }
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        setActiveTab(tab);
      }
    },
    []
  );

  /* ── File validation ── */
  const validateFile = useCallback(async (f: File): Promise<string | null> => {
    if (!ACCEPTED_TYPES.includes(f.type)) {
      return "Only JPG and PNG files are accepted.";
    }
    if (f.size > MAX_SIZE_BYTES) {
      return `File is too large. Maximum size is ${MAX_SIZE_MB} MB.`;
    }
    if (!(await validateMagicBytes(f))) {
      return "File does not appear to be a valid JPG or PNG image.";
    }
    return null;
  }, []);

  const handleFileSelect = useCallback(
    async (f: File) => {
      setUploadApiError(null);
      const err = await validateFile(f);
      if (err) {
        setFileError(err);
        setFile(null);
      } else {
        setFileError(null);
        setFile(f);
      }
    },
    [validateFile]
  );

  /* ── Drop handlers ── */
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);
  const onDragLeave = useCallback(() => setDragOver(false), []);
  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const f = e.dataTransfer.files[0];
      if (f) handleFileSelect(f);
    },
    [handleFileSelect]
  );
  const onInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (f) handleFileSelect(f);
    },
    [handleFileSelect]
  );

  /* ── Upload submit ── */
  const handleExtract = useCallback(async () => {
    if (!file) return;
    setUploading(true);
    setUploadApiError(null);
    try {
      const res = await extractPrescription(file);
      dispatch({ type: "SET_NEW_ROWS", rows: res.rows });
      navigate("/review/confirm");
    } catch (err) {
      if (err instanceof ApiRequestError && err.code === "OCR_UNAVAILABLE") {
        setUploadApiError(
          "Image reading is unavailable. Type the prescription instead."
        );
        // Auto-switch to type tab after short delay so the user sees the message
        setTimeout(() => setActiveTab("type"), 100);
      } else if (err instanceof ApiRequestError) {
        setUploadApiError(err.message);
      } else {
        setUploadApiError("Something went wrong. Please try again.");
      }
    } finally {
      setUploading(false);
    }
  }, [file, dispatch, navigate]);

  /* ── Type-tab row helpers ── */
  const addRow = useCallback(() => {
    setRows((prev) => [...prev, makeRow()]);
  }, []);

  const removeRow = useCallback((id: number) => {
    setRows((prev) => {
      const next = prev.filter((r) => r.id !== id);
      return next.length === 0 ? [makeRow()] : next;
    });
  }, []);

  const updateRow = useCallback(
    (id: number, patch: Partial<TypedRow>) => {
      setRows((prev) =>
        prev.map((r) => (r.id === id ? { ...r, ...patch } : r))
      );
    },
    []
  );

  /* ── Type submit ── */
  const handleNormalize = useCallback(async () => {
    const validRows = rows.filter((r) => r.name.trim().length > 0);
    if (validRows.length === 0) return;

    setTyping(true);
    setTypeApiError(null);

    const payload = validRows.map((r, i) => ({
      row_id: `N${i + 1}`,
      source: "NEW" as const,
      raw_text: r.name.trim(),
      dose: r.doseNotSpecified ? "Not specified" : r.dose.trim() || null,
      frequency: r.freqNotSpecified
        ? "Not specified"
        : r.frequency.trim() || null,
    }));

    try {
      const res = await normalizeMedicines({ rows: payload });
      dispatch({ type: "SET_NEW_ROWS", rows: res.rows });
      navigate("/review/confirm");
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setTypeApiError(err.message);
      } else {
        setTypeApiError("Something went wrong. Please try again.");
      }
    } finally {
      setTyping(false);
    }
  }, [rows, dispatch, navigate]);

  /* Reset the file input ref when file is cleared */
  useEffect(() => {
    if (!file && inputRef.current) inputRef.current.value = "";
  }, [file]);

  const hasValidName = rows.some((r) => r.name.trim().length > 0);

  /* ═══════════════════════════════════════════════════════
     Render
     ═══════════════════════════════════════════════════════ */
  return (
    <div className="min-h-screen bg-[var(--color-page)] px-4 py-8 flex flex-col items-center">
      {/* ── Tab bar ── */}
      <div
        className="flex gap-2 mb-6"
        role="tablist"
        aria-label="Prescription input method"
      >
        <TabButton
          active={activeTab === "upload"}
          onClick={() => setActiveTab("upload")}
          onKeyDown={(e) => handleTabKeyDown(e, "upload")}
          icon={<Image size={18} />}
          label="Upload"
          id="tab-upload"
          controls="panel-upload"
        />
        <TabButton
          active={activeTab === "type"}
          onClick={() => setActiveTab("type")}
          onKeyDown={(e) => handleTabKeyDown(e, "type")}
          icon={<ClipboardList size={18} />}
          label="Type"
          id="tab-type"
          controls="panel-type"
        />
      </div>

      {/* ── Upload panel ── */}
      {activeTab === "upload" && (
        <div
          id="panel-upload"
          role="tabpanel"
          aria-labelledby="tab-upload"
          className="w-full max-w-xl animate-fadeIn"
        >
          <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6">
            <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">
              Upload prescription
            </h2>

            {/* Dropzone */}
            <button
              type="button"
              className={`w-full border-2 border-dashed rounded-lg p-8 flex flex-col items-center gap-3
                transition-colors duration-150 cursor-pointer
                focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] focus-visible:ring-offset-2
                ${
                  dragOver
                    ? "border-[var(--color-primary)] bg-teal-50"
                    : "border-[var(--color-primary)] hover:bg-teal-50/60"
                }`}
              onClick={() => inputRef.current?.click()}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              aria-label="Upload prescription image"
            >
              <Upload size={32} className="text-[var(--color-primary)]" />
              <span className="text-[var(--color-text)] font-medium text-sm">
                Click to upload or drag &amp; drop
              </span>
              <span className="text-[var(--color-text-2)] text-xs">
                PNG, JPG up to {MAX_SIZE_MB} MB
              </span>
              <input
                ref={inputRef}
                type="file"
                accept=".jpg,.jpeg,.png"
                className="hidden"
                onChange={onInputChange}
                aria-hidden="true"
                tabIndex={-1}
              />
            </button>

            {/* Selected file chip */}
            {file && (
              <div className="mt-3 flex items-center gap-2 text-sm text-[var(--color-text)]">
                <Image size={14} className="text-[var(--color-primary)] shrink-0" />
                <span className="truncate">{file.name}</span>
                <span className="text-[var(--color-text-2)]">
                  ({(file.size / 1024 / 1024).toFixed(1)} MB)
                </span>
                <button
                  onClick={() => { setFile(null); setFileError(null); }}
                  className="ml-auto text-[var(--color-text-2)] hover:text-red-600 transition-colors p-1 rounded
                    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]"
                  aria-label="Remove selected file"
                >
                  <X size={14} />
                </button>
              </div>
            )}

            {/* File validation error */}
            {fileError && <InlineError message={fileError} />}

            {/* API error with retry */}
            {uploadApiError && (
              <InlineError message={uploadApiError}>
                <button
                  onClick={handleExtract}
                  className="text-[var(--color-primary)] hover:underline text-xs font-medium
                    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] rounded"
                >
                  Retry
                </button>
              </InlineError>
            )}

            {/* Submit */}
            <PrimaryButton
              onClick={handleExtract}
              disabled={!file || !!fileError || uploading}
              loading={uploading}
              className="mt-5"
            >
              Extract and review
            </PrimaryButton>
          </div>
        </div>
      )}

      {/* ── Type panel ── */}
      {activeTab === "type" && (
        <div
          id="panel-type"
          role="tabpanel"
          aria-labelledby="tab-type"
          className="w-full max-w-xl animate-fadeIn"
        >
          <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6">
            {/* Header */}
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-[var(--color-text)]">
                Medications ({rows.length})
              </h2>
              <button
                onClick={addRow}
                className="text-[var(--color-primary)] text-sm font-medium hover:text-[var(--color-primary-hover)] transition-colors
                  focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] rounded px-2 py-1"
              >
                + Add
              </button>
            </div>

            {/* Rows */}
            <div className="space-y-4">
              {rows.map((row) => (
                <MedicationInputRow
                  key={row.id}
                  row={row}
                  onUpdate={(patch) => updateRow(row.id, patch)}
                  onRemove={() => removeRow(row.id)}
                />
              ))}
            </div>

            {/* API error */}
            {typeApiError && (
              <InlineError message={typeApiError}>
                <button
                  onClick={handleNormalize}
                  className="text-[var(--color-primary)] hover:underline text-xs font-medium
                    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] rounded"
                >
                  Retry
                </button>
              </InlineError>
            )}

            {/* Submit */}
            <PrimaryButton
              onClick={handleNormalize}
              disabled={!hasValidName || typing}
              loading={typing}
              className="mt-5"
            >
              Review medicines
            </PrimaryButton>
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════
   Sub-components
   ═══════════════════════════════════════════════════════ */

/* ── Tab button ── */
function TabButton({
  active,
  onClick,
  onKeyDown,
  icon,
  label,
  id,
  controls,
}: {
  active: boolean;
  onClick: () => void;
  onKeyDown: (e: KeyboardEvent) => void;
  icon: React.ReactNode;
  label: string;
  id: string;
  controls: string;
}) {
  return (
    <button
      id={id}
      role="tab"
      aria-selected={active}
      aria-controls={controls}
      tabIndex={active ? 0 : -1}
      onClick={onClick}
      onKeyDown={onKeyDown}
      className={`flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition-all duration-150
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] focus-visible:ring-offset-2
        ${
          active
            ? "bg-[var(--color-text)] text-white shadow-sm"
            : "text-[var(--color-text-2)] hover:bg-gray-100"
        }`}
    >
      {icon}
      {label}
    </button>
  );
}

/* ── Single medication input row ── */
function MedicationInputRow({
  row,
  onUpdate,
  onRemove,
}: {
  row: TypedRow;
  onUpdate: (patch: Partial<TypedRow>) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-col lg:flex-row gap-3 p-3 border border-[var(--color-border)] rounded-lg bg-gray-50/50">
      {/* Name */}
      <div className="flex-1 min-w-0">
        <label className="sr-only" htmlFor={`med-name-${row.id}`}>
          Medicine name
        </label>
        <input
          id={`med-name-${row.id}`}
          type="text"
          placeholder="Enter medicine name"
          value={row.name}
          onChange={(e) => onUpdate({ name: e.target.value })}
          className="w-full px-3 py-2 border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)]
            bg-white placeholder:text-[var(--color-text-2)]
            focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] focus:border-transparent transition-shadow"
        />
      </div>

      {/* Dose */}
      <div className="w-full lg:w-36">
        <label className="sr-only" htmlFor={`med-dose-${row.id}`}>
          Dose
        </label>
        <input
          id={`med-dose-${row.id}`}
          type="text"
          placeholder="Dose"
          value={row.doseNotSpecified ? "" : row.dose}
          disabled={row.doseNotSpecified}
          onChange={(e) => onUpdate({ dose: e.target.value })}
          className="w-full px-3 py-2 border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)]
            bg-white placeholder:text-[var(--color-text-2)] disabled:bg-gray-100 disabled:text-[var(--color-text-2)]
            focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] focus:border-transparent transition-shadow"
        />
        <label className="flex items-center gap-1.5 mt-1 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={row.doseNotSpecified}
            onChange={(e) =>
              onUpdate({ doseNotSpecified: e.target.checked, dose: "" })
            }
            className="rounded border-gray-300 text-[var(--color-primary)]
              focus:ring-[var(--color-primary)] h-3.5 w-3.5"
          />
          <span className="text-[10px] text-[var(--color-text-2)]">
            Not specified
          </span>
        </label>
      </div>

      {/* Frequency */}
      <div className="w-full lg:w-40">
        <label className="sr-only" htmlFor={`med-freq-${row.id}`}>
          Frequency
        </label>
        <input
          id={`med-freq-${row.id}`}
          type="text"
          placeholder="Frequency"
          value={row.freqNotSpecified ? "" : row.frequency}
          disabled={row.freqNotSpecified}
          onChange={(e) => onUpdate({ frequency: e.target.value })}
          className="w-full px-3 py-2 border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)]
            bg-white placeholder:text-[var(--color-text-2)] disabled:bg-gray-100 disabled:text-[var(--color-text-2)]
            focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] focus:border-transparent transition-shadow"
        />
        <label className="flex items-center gap-1.5 mt-1 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={row.freqNotSpecified}
            onChange={(e) =>
              onUpdate({ freqNotSpecified: e.target.checked, frequency: "" })
            }
            className="rounded border-gray-300 text-[var(--color-primary)]
              focus:ring-[var(--color-primary)] h-3.5 w-3.5"
          />
          <span className="text-[10px] text-[var(--color-text-2)]">
            Not specified
          </span>
        </label>
      </div>

      {/* Remove */}
      <button
        onClick={onRemove}
        aria-label="Remove medication row"
        className="self-start lg:self-center p-2 text-[var(--color-text-2)] hover:text-red-600 rounded-lg transition-colors
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]"
      >
        <X size={16} />
      </button>
    </div>
  );
}

/* ── Primary action button ── */
function PrimaryButton({
  children,
  onClick,
  disabled,
  loading,
  className = "",
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  loading?: boolean;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg text-sm font-semibold text-white
        transition-all duration-150
        bg-[var(--color-primary)] hover:bg-[var(--color-primary-hover)]
        disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-[var(--color-primary)]
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] focus-visible:ring-offset-2
        ${className}`}
    >
      {loading && <Loader2 size={16} className="animate-spin" />}
      {children}
    </button>
  );
}

/* ── Inline error notice ── */
function InlineError({
  message,
  children,
}: {
  message: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className="mt-3 flex items-start gap-2 p-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700"
      role="alert"
    >
      <AlertCircle size={16} className="shrink-0 mt-0.5" />
      <div className="flex-1">
        <p>{message}</p>
        {children && <div className="mt-1">{children}</div>}
      </div>
    </div>
  );
}
