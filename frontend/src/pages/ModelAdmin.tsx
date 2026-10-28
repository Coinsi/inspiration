import { Link } from "react-router-dom";
import { ArrowLeft, Server } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { ModelChannels } from "@/components/ModelChannels";
import { AccountMenu } from "@/components/AccountMenu";

export default function ModelAdmin() {
  const { me, loading } = useAuth();
  if (loading) return <div className="p-8">正在加载…</div>;
  if (!me?.user.is_platform_admin)
    return <main className="p-8 space-y-4"><h1 className="text-xl font-semibold">仅平台管理员可管理全局模型</h1><Link to="/projects" className="underline">返回项目</Link></main>;
  return (
    <main className="min-h-screen bg-background">
      <header className="border-b px-6 py-4 flex items-center justify-between gap-4">
        <Link to="/projects" className="flex items-center gap-2 text-sm text-muted-foreground"><ArrowLeft size={16} />返回项目</Link>
        <AccountMenu />
      </header>
      <div className="max-w-6xl mx-auto px-6 py-8 md:py-12">
        <div className="mb-8 space-y-3">
          <h1 className="text-2xl font-semibold flex items-center gap-3"><Server size={24} />全局模型配置</h1>
          <p className="text-sm text-muted-foreground leading-6 max-w-3xl">统一配置文字、图片与视频模型，用户进入项目即可使用，无需填写密钥。个人模型和项目专用服务仍可单独配置。</p>
        </div>
        <ModelChannels projectId="platform" />
      </div>
    </main>
  );
}
