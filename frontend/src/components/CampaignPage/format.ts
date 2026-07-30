import { ApiError } from "../../api/client";

/** Display fallback when the offer lookup misses: "2026-2:22000270:M1" -> "M1". */
export function sectionLabelFromId(id: string): string {
  const parts = id.split(":");
  return parts[parts.length - 1] || id;
}

/** Friendly messages for the campaign error vocabulary (envelope {error:{code,message,details}}).
 * CAMPAIGN_EXISTS is handled where it happens (open the existing one) — not here. */
export function friendlyCampaignError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "ROUND_LIMIT_EXCEEDED") {
      const max = err.details?.max_adds;
      const current = err.details?.current;
      const counts =
        typeof max === "number"
          ? ` (máx. ${max}${typeof current === "number" ? `, já há ${current}` : ""})`
          : "";
      return `Limite de adições da rodada atingido${counts} — regra dura do Cobalto. Apague um pedido antes de adicionar outro.`;
    }
    if (err.code === "INVALID_TRANSITION") {
      const from = typeof err.details?.from === "string" ? err.details.from : "?";
      const allowed = Array.isArray(err.details?.allowed) ? err.details.allowed.join(", ") : "";
      return allowed
        ? `Transição inválida a partir de "${from}" — permitidas: ${allowed}.`
        : `Pedido em estado terminal ("${from}") é histórico e não muda mais de status.`;
    }
    return err.message;
  }
  return err instanceof Error ? err.message : "Falha inesperada ao falar com o servidor.";
}
