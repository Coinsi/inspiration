import { createContext, useContext, useEffect } from "react";
export const AdminUnsavedContext = createContext<(dirty: boolean) => void>(
  () => {},
);
export function useAdminUnsaved(dirty: boolean) {
  const set = useContext(AdminUnsavedContext);
  useEffect(() => {
    set(dirty);
    return () => set(false);
  }, [dirty, set]);
}
