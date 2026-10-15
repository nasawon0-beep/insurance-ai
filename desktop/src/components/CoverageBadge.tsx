type CoverageBadgeProps = {
  status: string;
};

const STATUS_CLASS: Record<string, string> = {
  가입: "coverage-badge--sufficient",
  미가입: "coverage-badge--uninsured",
  충분: "coverage-badge--sufficient",
  부족: "coverage-badge--shortage",
  확인필요: "coverage-badge--unknown",
};

export default function CoverageBadge({ status }: CoverageBadgeProps) {
  return <span className={`coverage-badge ${STATUS_CLASS[status] ?? "coverage-badge--unknown"}`}>{status}</span>;
}
