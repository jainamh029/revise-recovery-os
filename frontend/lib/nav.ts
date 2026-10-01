import { BASE_PATH, DEMO } from "./demo";

/** Cohort detail URL. A static host cannot serve unknown /cohorts/<id> paths, so the demo build uses /cohort?id=<id>. */
export const cohortHref = (id: string) => (DEMO ? `/cohort?id=${encodeURIComponent(id)}` : `/cohorts/${id}`);

/** Backend payloads carry hrefs like /cohorts/<id>; map them to the right URL for this build. */
export const normalizeHref = (h: string | null | undefined) => {
  if (!h) return h ?? undefined;
  const m = /^\/cohorts\/([^/?#]+)$/.exec(h);
  return m ? cohortHref(m[1]) : h;
};

export const assetUrl = (p: string) => `${BASE_PATH}${p}`;
