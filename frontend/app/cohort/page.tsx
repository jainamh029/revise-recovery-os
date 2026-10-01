"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import CohortDetail from "@/components/cohort/CohortDetail";
import { ErrorBox, Loading } from "@/components/ui";

/** /cohort?id=<id> -- the cohort page for builds that cannot serve arbitrary /cohorts/<id> paths (static GitHub Pages demo). */
function Pick() {
  const id = useSearchParams().get("id");
  return id ? <CohortDetail id={id} /> : <ErrorBox message="No cohort selected. Open a cohort from the Underwriting list." />;
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Pick /></Suspense>;
}
