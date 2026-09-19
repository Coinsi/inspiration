import { Link, useParams } from "react-router-dom";
import { SourceImporter } from "@/components/SourceImporter";
import { useAuth } from "@/lib/auth";
export default function AssetImport() {
  const { projectId } = useParams();
  const role = useAuth().me?.memberships.find(
    (m) => m.project_id === projectId,
  )?.role;
  return (
    <div className="studio-page">
      <header className="page-heading">
        <div>
          <h1>导入外部目录</h1>
          <p>将已有角色、场景和道具资料整理到当前项目。</p>
        </div>
        <Link className="studio-link" to={`/projects/${projectId}/assets`}>
          返回资产库
        </Link>
      </header>
      {role && ["admin", "director", "artist"].includes(role) ? (
        <SourceImporter key={projectId} base={`/projects/${projectId}`} />
      ) : (
        <p className="studio-empty">
          当前角色可浏览资产，请联系项目管理员或美术成员导入目录。
        </p>
      )}
    </div>
  );
}
