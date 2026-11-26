"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setNonce } from "get-nonce";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { Toaster } from "./ui/toaster";
import { TooltipProvider } from "./ui/overlay";

export function Providers({ children, nonce }: { children: React.ReactNode; nonce?: string }) {
  // Radix's scroll lock injects a <style> element; give it this request's CSP nonce.
  if (nonce) setNonce(nonce);
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 20_000,
            refetchOnWindowFocus: true,
            retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={250}>
        {children}
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
