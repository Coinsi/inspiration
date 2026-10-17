import { Navigate, useParams } from "react-router-dom";

export type GeneratedMaterial = {
  work?: { id: string; title: string; script_id: string } | null;
  id: string;
  job_id: string;
  title: string;
  description?: string;
  tags?: string[] | null;
  metadata_revision?: number;
  poster_hash?: string | null;
  prompt: string;
  output_type: "image" | "video";
  reference_available: boolean;
  blob_hash: string;
  model_name: string;
  provider: string;
  created_at: string;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  size_bytes: number;
  target_type: string;
  target_id: string;
};

export default function GeneratedLibrary() {
  const { projectId } = useParams();
  return (
    <Navigate
      replace
      to={`/projects/${projectId}/materials?source=generated`}
    />
  );
}
