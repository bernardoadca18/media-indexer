export function statusLabel(value: string) {
  const labels: Record<string, string> = {
    ready: "Pronto",
    pending: "Pendente",
    processing: "Em processamento",
    running: "Em processamento",
    completed: "Concluído",
    failed: "Falhou",
    queued: "Na fila",
  };
  return labels[value] || value.replace(/_/g, " ");
}
