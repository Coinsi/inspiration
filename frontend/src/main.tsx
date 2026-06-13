import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";
import { AuthProvider } from "./lib/auth";
import { ThemeProvider } from "./lib/theme";
import { I18nProvider } from "./lib/i18n";
import { ToastProvider } from "./components/ui/toast";
import { ConfirmProvider } from "./components/ui/confirm";
import { BackgroundTaskProvider } from "./components/BackgroundTasks";

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <I18nProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <ToastProvider>
              <ConfirmProvider>
                <BackgroundTaskProvider>
                  <AuthProvider>
                    <App />
                  </AuthProvider>
                </BackgroundTaskProvider>
              </ConfirmProvider>
            </ToastProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </I18nProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
