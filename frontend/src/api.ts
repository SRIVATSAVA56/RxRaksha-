/* ───────────────────────────────────────────────────────
   api.ts — API client for RxRaksha backend
   ─────────────────────────────────────────────────────── */

import type { MedicineRow } from "./types";

const API = "/api";

/** Structured error from the backend */
export class ApiRequestError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
    this.name = "ApiRequestError";
  }
}

/** Parse a JSON error body, or fall back to a generic message */
async function parseError(res: Response): Promise<ApiRequestError> {
  try {
    const body = await res.json();
    if (body?.error?.code && body?.error?.message) {
      return new ApiRequestError(body.error.code, body.error.message);
    }
  } catch {
    // not JSON
  }
  return new ApiRequestError(
    `HTTP_${res.status}`,
    `Request failed (${res.status}). Please try again.`
  );
}

/* ── Extract prescription from image ── */

export interface ExtractResponse {
  extraction_id: string;
  rows: MedicineRow[];
}

export async function extractPrescription(
  file: File
): Promise<ExtractResponse> {
  const form = new FormData();
  form.append("image", file);

  const res = await fetch(`${API}/prescriptions/extract`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) throw await parseError(res);
  return res.json();
}

/* ── Normalize typed medicines ── */

export interface NormalizeRequest {
  rows: {
    row_id: string;
    source: "NEW";
    raw_text: string;
    dose?: string | null;
    frequency?: string | null;
  }[];
}

export interface NormalizeResponse {
  rows: MedicineRow[];
}

export async function normalizeMedicines(
  req: NormalizeRequest
): Promise<NormalizeResponse> {
  const res = await fetch(`${API}/medicines/normalize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

  if (!res.ok) throw await parseError(res);
  return res.json();
}
