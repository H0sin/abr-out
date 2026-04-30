// Minimal typings for window.Telegram.WebApp (only what we use).
export {};

declare global {
  interface TelegramWebAppUser {
    id: number;
    username?: string;
    first_name?: string;
    last_name?: string;
  }

  interface TelegramWebApp {
    initData: string;
    initDataUnsafe: { user?: TelegramWebAppUser };
    ready(): void;
    expand(): void;
    close(): void;
    showAlert(message: string): void;
    colorScheme: "light" | "dark";
    themeParams: Record<string, string>;
    HapticFeedback?: {
      notificationOccurred(type: "error" | "success" | "warning"): void;
      impactOccurred(style: "light" | "medium" | "heavy" | "rigid" | "soft"): void;
    };
  }

  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}
