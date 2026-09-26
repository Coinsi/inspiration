import { Link, Outlet } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

export default function Layout() {
  const { me, logout, logoutError } = useAuth();
  return (
    <div className="min-h-screen">
      <header className="border-b border-border">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <Link to="/projects" className="font-semibold">
            Inspiration
          </Link>
          <div className="flex items-center gap-3">
            {logoutError && <span role="alert">{logoutError}</span>}
            <span className="text-sm text-muted-foreground">{me?.user.display_name}</span>
            <Button variant="outline" size="sm" onClick={logout}>
              退出
            </Button>
          </div>
        </div>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}
