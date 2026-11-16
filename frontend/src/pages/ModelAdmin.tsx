import { ModelChannels } from "@/components/ModelChannels";
import { Heading } from "./admin/shared";
import { useAuth } from "@/lib/auth";
export default function ModelAdmin() {
  const { me } = useAuth();
  if (!me?.user.is_platform_admin) return <h1>仅平台管理员可管理全局模型</h1>;
  return (
    <>
      <Heading
        title="全局模型配置"
        description="统一配置平台提供的文字、图片与视频模型。个人服务和项目专用连接仍保持独立。"
      />
      <ModelChannels projectId="platform" />
    </>
  );
}
