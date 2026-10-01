"use client";
import { useParams } from "next/navigation";
import CohortDetail from "@/components/cohort/CohortDetail";

export default function Page() {
  const { id } = useParams<{ id: string }>();
  return <CohortDetail id={id} />;
}
