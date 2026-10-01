"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import DrillPage from "@/components/DrillPage";
import { Loading } from "@/components/ui";

function Pick() {
  const m = useSearchParams().get("metric");
  return <DrillPage metric={m === "forecast_cm" ? "forecast_cm" : "realized_cm"} />;
}
export default function Page() {
  return <Suspense fallback={<Loading />}><Pick /></Suspense>;
}
