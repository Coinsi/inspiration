/** Preserve the workspace's shape while its code or data arrives. */
export function WorkspaceLoading() {
  return <div className="workspace-loading" role="status" aria-label="正在打开工作区">
    <span className="sr-only">正在打开工作区</span>
    <div className="workspace-loading-title" />
    <div className="workspace-loading-toolbar" />
    <div className="workspace-loading-grid">{[0, 1, 2].map(i => <div key={i} />)}</div>
  </div>;
}
