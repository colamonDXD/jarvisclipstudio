/**
 * The pre-processing summary on the create page (F59): one plain
 * line, shown before a task is started, saying what the run will be
 * made from and with which choices. Every value comes from the
 * create form's own state — the summary adds no data of its own and
 * makes no API calls. The Source part appears only once a source
 * exists (a pasted link or a chosen file); the choice parts always
 * show, because the form always holds a value for each of them.
 */
export interface CreateSummaryValues {
  sourceLabel: string | null;
  creationModeLabel: string;
  templateLabel: string;
  framingLabel: string;
  nicheLabel: string;
  outputFormatLabel: string;
}

export interface CreateSummaryPart {
  label: string;
  value: string;
}

export function createSummaryParts(
  values: CreateSummaryValues
): CreateSummaryPart[] {
  const parts: CreateSummaryPart[] = [];
  if (values.sourceLabel) {
    parts.push({ label: "Source", value: values.sourceLabel });
  }
  parts.push({ label: "Mode", value: values.creationModeLabel });
  parts.push({ label: "Template", value: values.templateLabel });
  parts.push({ label: "Framing", value: values.framingLabel });
  parts.push({ label: "Style", value: values.nicheLabel });
  parts.push({ label: "Format", value: values.outputFormatLabel });
  return parts;
}
