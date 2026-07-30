'use client';

import { createContext, useContext } from 'react';

import type { ClaimRow, ReportModel } from '@/lib/report-model';

/** The axes the claim explorer can be filtered on from elsewhere in the report. */
export interface ClaimFilter {
  state?: string;
  type?: string;
  category?: string;
  severity?: string;
}

export interface ReportContextValue {
  model: ReportModel;
  /** Opens the claim detail drawer. Ignored when the id is unknown. */
  openClaim: (claimId: string) => void;
  /** Anchors the citation popover to an element. */
  openCitation: (ref: string, anchor: HTMLElement, pinned: boolean) => void;
  closeCitation: (immediate?: boolean) => void;
  /** Smoothly scrolls a top-level report section into view. */
  goToSection: (sectionId: string) => void;
  /** Pre-seeds the claim explorer's filters, then scrolls to it. */
  filterClaims: (filter: ClaimFilter) => void;
  lookupClaim: (claimId: string) => ClaimRow | undefined;
}

const ReportContext = createContext<ReportContextValue | null>(null);

export const ReportProvider = ReportContext.Provider;

export function useReport(): ReportContextValue {
  const value = useContext(ReportContext);
  if (!value) throw new Error('useReport must be used inside a ReportProvider');
  return value;
}

export function useReportModel(): ReportModel {
  return useReport().model;
}
