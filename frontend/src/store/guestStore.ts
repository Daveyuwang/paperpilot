import { nanoid } from "./nanoid";

const GUEST_ID_KEY = "pp_guest_id";

export function getGuestId(): string {
  const existing = localStorage.getItem(GUEST_ID_KEY);
  if (existing && existing.trim()) return existing;

  const guestId = `guest_${nanoid(18)}`;
  localStorage.setItem(GUEST_ID_KEY, guestId);
  return guestId;
}
