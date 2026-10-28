export type FamilyRelationshipCode = "SPOUSE" | "PARENT" | "CHILD" | "SIBLING" | "OTHER";

export type FamilyCustomerSummary = {
  id: string;
  name: string;
  phone: string | null;
  birth_date: string | null;
  gender?: string | null;
  customer_status?: string | null;
};

export type FamilyRelationship = {
  id: string;
  relationship: FamilyRelationshipCode;
  relationship_label: string;
  other_description: string | null;
  customer: FamilyCustomerSummary;
  created_at: string;
  updated_at: string;
};

export type FamilyFormState = {
  relatedCustomerId: string;
  relationship: FamilyRelationshipCode | "";
  otherDescription: string;
  coParentCustomerId: string;
  sharedChildIds: string[];
};

export type FamilyApi = (path: string, init?: RequestInit) => Promise<any>;

export type FamilyRegistrationDraft = {
  customer: FamilyCustomerSummary;
  form: FamilyFormState;
};

export function createFamilyBatchIdempotencyKey(): string {
  return `family-registration:${globalThis.crypto.randomUUID()}`;
}

export function buildFamilyPayload(form: FamilyFormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    related_customer_id: form.relatedCustomerId,
    relationship: form.relationship,
  };
  const description = form.otherDescription.trim();
  if (form.relationship === "OTHER" && description) payload.other_description = description;
  if (form.relationship === "CHILD" && form.coParentCustomerId) {
    payload.co_parent_customer_id = form.coParentCustomerId;
  }
  if (form.relationship === "SPOUSE" && form.sharedChildIds.length) {
    payload.shared_child_ids = [...form.sharedChildIds];
  }
  return payload;
}

export function eligibleSharedChildren(rows: FamilyRelationship[]): FamilyRelationship[] {
  return rows.filter((row) => row.relationship === "CHILD");
}

export function spouseRelationships(rows: FamilyRelationship[]): FamilyRelationship[] {
  return rows.filter((row) => row.relationship === "SPOUSE");
}

export async function saveFamilyRelationship(
  api: FamilyApi,
  customerId: string,
  form: FamilyFormState,
  existing: readonly FamilyRelationship[],
): Promise<FamilyRelationship[]> {
  const response = await api(`/customers/${customerId}/family-relationships`, {
    method: "POST",
    body: JSON.stringify(buildFamilyPayload(form)),
  });
  const next = response?.family_relationships;
  return Array.isArray(next) ? next : [...existing];
}

export async function saveRegistrationFamilyDrafts(
  api: FamilyApi,
  customerId: string,
  drafts: readonly FamilyRegistrationDraft[],
  idempotencyKey: string,
): Promise<FamilyRelationship[]> {
  if (drafts.length === 0) return [];
  const response = await api(`/customers/${customerId}/family-relationships/batch`, {
    method: "POST",
    body: JSON.stringify({
      idempotency_key: idempotencyKey,
      relationships: drafts.map((draft) => buildFamilyPayload(draft.form)),
    }),
  });
  return Array.isArray(response?.family_relationships) ? response.family_relationships : [];
}
