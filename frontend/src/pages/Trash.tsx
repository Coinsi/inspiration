import { Navigate, useParams } from "react-router-dom";
export default function Trash() {
  const { projectId } = useParams();
  return <Navigate replace to={`/projects/${projectId}/materials?tab=trash`} />;
}
