const BLOCKED_PHRASES = [
  'guaranteed winner',
  'lock of the day',
  'sure thing',
  'cannot lose',
  'risk free',
  'free money'
];

export function validatePublicCopy(copy: string) {
  const lowerCopy = copy.toLowerCase();
  const violations = BLOCKED_PHRASES.filter((phrase) => lowerCopy.includes(phrase));

  return {
    valid: violations.length === 0,
    violations,
    approvedReplacement: violations.length
      ? 'Use InQsi leans, market support detected, signal strength, what to watch, and review before you lock it in.'
      : null
  };
}

export const INQSI_RESPONSIBLE_USE_COPY = {
  positioning: 'InQsi is a sports market intelligence and review platform.',
  noGuarantee: 'InQsi does not guarantee outcomes.',
  dataPolicy: 'If verified data is unavailable, InQsi shows Working on it.',
  userDecision: 'Users are responsible for their own decisions.'
};
