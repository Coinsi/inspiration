import { Github, ArrowUpRight } from "lucide-react";

export function GitHubLink({ onClick }: { onClick?: () => void }) {
  return (
    <a
      className="site-github-link"
      href="https://github.com/Coinsi/inspiration"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="GitHub 项目仓库（新标签页打开）"
      onClick={onClick}
    >
      <Github size={15} aria-hidden="true" />
      GitHub
      <ArrowUpRight size={12} aria-hidden="true" />
    </a>
  );
}
