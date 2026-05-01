import { useEffect, useRef } from "react";

const tg = () => window.Telegram?.WebApp;

export const haptic = {
  success: () => tg()?.HapticFeedback?.notificationOccurred("success"),
  error: () => tg()?.HapticFeedback?.notificationOccurred("error"),
  warning: () => tg()?.HapticFeedback?.notificationOccurred("warning"),
  light: () => tg()?.HapticFeedback?.impactOccurred("light"),
  medium: () => tg()?.HapticFeedback?.impactOccurred("medium"),
  selection: () => tg()?.HapticFeedback?.selectionChanged(),
};

type MainButtonOpts = {
  text: string;
  onClick: () => void;
  visible?: boolean;
  loading?: boolean;
  disabled?: boolean;
};

export function useMainButton(opts: MainButtonOpts | null) {
  // Keep latest onClick in a ref so we don't repeatedly attach/detach.
  const cbRef = useRef<() => void>(() => {});

  useEffect(() => {
    cbRef.current = opts?.onClick ?? (() => {});
  }, [opts?.onClick]);

  useEffect(() => {
    const btn = tg()?.MainButton;
    if (!btn || !opts) return;
    const handler = () => cbRef.current();
    btn.onClick(handler);
    return () => {
      btn.offClick(handler);
      btn.hide();
      btn.hideProgress();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(opts)]);

  useEffect(() => {
    const btn = tg()?.MainButton;
    if (!btn) return;
    if (!opts || opts.visible === false) {
      btn.hide();
      btn.hideProgress();
      return;
    }
    btn.setText(opts.text);
    if (opts.disabled) btn.disable();
    else btn.enable();
    if (opts.loading) btn.showProgress(true);
    else btn.hideProgress();
    btn.show();
  }, [opts?.text, opts?.visible, opts?.loading, opts?.disabled]);
}

export function useBackButton(onClick: (() => void) | null) {
  const cbRef = useRef<() => void>(() => {});
  useEffect(() => {
    cbRef.current = onClick ?? (() => {});
  }, [onClick]);

  useEffect(() => {
    const btn = tg()?.BackButton;
    if (!btn) return;
    if (!onClick) {
      btn.hide();
      return;
    }
    const h = () => cbRef.current();
    btn.onClick(h);
    btn.show();
    return () => {
      btn.offClick(h);
      btn.hide();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(onClick)]);
}

export function showConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const t = tg();
    if (t?.showConfirm) {
      t.showConfirm(message, (ok) => resolve(Boolean(ok)));
    } else {
      resolve(window.confirm(message));
    }
  });
}

/** Open a t.me link inside the Telegram client (or fallback to a new tab). */
export function openTelegramLink(url: string): void {
  const t = tg() as unknown as { openTelegramLink?: (u: string) => void } | undefined;
  if (t?.openTelegramLink) {
    t.openTelegramLink(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}
