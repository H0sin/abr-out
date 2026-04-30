// Typings for the subset of window.Telegram.WebApp we use.
/// <reference types="vite/client" />
export {};

declare global {
  interface TelegramWebAppUser {
    id: number;
    username?: string;
    first_name?: string;
    last_name?: string;
    language_code?: string;
    photo_url?: string;
  }

  interface TelegramMainButton {
    text: string;
    color: string;
    textColor: string;
    isVisible: boolean;
    isActive: boolean;
    isProgressVisible: boolean;
    setText(text: string): TelegramMainButton;
    onClick(cb: () => void): TelegramMainButton;
    offClick(cb: () => void): TelegramMainButton;
    show(): TelegramMainButton;
    hide(): TelegramMainButton;
    enable(): TelegramMainButton;
    disable(): TelegramMainButton;
    showProgress(leaveActive?: boolean): TelegramMainButton;
    hideProgress(): TelegramMainButton;
    setParams(p: {
      text?: string;
      color?: string;
      text_color?: string;
      is_active?: boolean;
      is_visible?: boolean;
    }): TelegramMainButton;
  }

  interface TelegramBackButton {
    isVisible: boolean;
    onClick(cb: () => void): TelegramBackButton;
    offClick(cb: () => void): TelegramBackButton;
    show(): TelegramBackButton;
    hide(): TelegramBackButton;
  }

  interface TelegramWebApp {
    initData: string;
    initDataUnsafe: { user?: TelegramWebAppUser; start_param?: string };
    version: string;
    platform: string;
    colorScheme: "light" | "dark";
    themeParams: Record<string, string>;
    MainButton: TelegramMainButton;
    BackButton: TelegramBackButton;
    HapticFeedback?: {
      notificationOccurred(type: "error" | "success" | "warning"): void;
      impactOccurred(
        style: "light" | "medium" | "heavy" | "rigid" | "soft",
      ): void;
      selectionChanged(): void;
    };
    ready(): void;
    expand(): void;
    close(): void;
    showAlert(message: string, cb?: () => void): void;
    showConfirm(message: string, cb?: (ok: boolean) => void): void;
    setHeaderColor(color: string): void;
    setBackgroundColor(color: string): void;
    enableClosingConfirmation(): void;
    disableClosingConfirmation(): void;
    onEvent(event: string, cb: () => void): void;
    offEvent(event: string, cb: () => void): void;
    openTelegramLink(url: string): void;
    openLink(url: string, options?: { try_instant_view?: boolean }): void;
  }

  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}
