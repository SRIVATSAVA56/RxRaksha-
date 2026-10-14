/* ───────────────────────────────────────────────────────
   state.tsx — Global state via useReducer + context
   ─────────────────────────────────────────────────────── */

import {
  createContext,
  useContext,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import type { Patient, MedicineRow, AnalysisResult } from "./types";

/* ── State shape ── */
export interface AppState {
  patient: Patient;
  existingRows: MedicineRow[];
  newRows: MedicineRow[];
  analysis: AnalysisResult | null;
  stale: boolean;
  llmEnabled: boolean;
  reviewNotes: Record<string, string>; // finding_id -> note (browser memory only)
}

const defaultPatient: Patient = {
  label: "",
  age_years: null,
  weight_kg: null,
  gender: null,
  kidney_status: "UNKNOWN",
  liver_status: "UNKNOWN",
  allergy_status: "UNKNOWN",
  allergies: [],
  diagnosis_status: "UNKNOWN",
  diagnoses: [],
};

const initialState: AppState = {
  patient: defaultPatient,
  existingRows: [],
  newRows: [],
  analysis: null,
  stale: false,
  llmEnabled: false,
  reviewNotes: {},
};

/* ── Actions ── */
export type Action =
  | { type: "SET_PATIENT"; patient: Patient }
  | { type: "SET_EXISTING_ROWS"; rows: MedicineRow[] }
  | { type: "SET_NEW_ROWS"; rows: MedicineRow[] }
  | { type: "UPDATE_ROW"; row: MedicineRow }
  | { type: "SET_ANALYSIS"; analysis: AnalysisResult }
  | { type: "CLEAR_ANALYSIS" }
  | { type: "MARK_STALE" }
  | { type: "TOGGLE_LLM" }
  | { type: "SET_REVIEW_NOTE"; findingId: string; note: string }
  | { type: "LOAD_DEMO"; patient: Patient; existingRows: MedicineRow[]; newRows: MedicineRow[] };

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SET_PATIENT":
      return {
        ...state,
        patient: action.patient,
        stale: state.analysis !== null,
      };

    case "SET_EXISTING_ROWS":
      return {
        ...state,
        existingRows: action.rows,
        stale: state.analysis !== null,
      };

    case "SET_NEW_ROWS":
      return {
        ...state,
        newRows: action.rows,
        stale: state.analysis !== null,
      };

    case "UPDATE_ROW": {
      const updateIn = (rows: MedicineRow[]) =>
        rows.map((r) => (r.row_id === action.row.row_id ? action.row : r));
      return {
        ...state,
        existingRows: updateIn(state.existingRows),
        newRows: updateIn(state.newRows),
        stale: state.analysis !== null,
      };
    }

    case "SET_ANALYSIS":
      return { ...state, analysis: action.analysis, stale: false };

    case "CLEAR_ANALYSIS":
      return { ...state, analysis: null, stale: false };

    case "MARK_STALE":
      return { ...state, stale: true };

    case "TOGGLE_LLM":
      return { ...state, llmEnabled: !state.llmEnabled };

    case "SET_REVIEW_NOTE":
      return {
        ...state,
        reviewNotes: {
          ...state.reviewNotes,
          [action.findingId]: action.note,
        },
      };

    case "LOAD_DEMO":
      return {
        ...state,
        patient: action.patient,
        existingRows: action.existingRows,
        newRows: action.newRows,
        analysis: null,
        stale: false,
      };

    default:
      return state;
  }
}

/* ── Context ── */
const StateCtx = createContext<AppState>(initialState);
const DispatchCtx = createContext<Dispatch<Action>>(() => {});

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <StateCtx.Provider value={state}>
      <DispatchCtx.Provider value={dispatch}>{children}</DispatchCtx.Provider>
    </StateCtx.Provider>
  );
}

export function useAppState() {
  return useContext(StateCtx);
}

export function useAppDispatch() {
  return useContext(DispatchCtx);
}
