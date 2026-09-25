type CoverageBadgeProps = {
  status: string;
};

const STATUS_CLASS: Record<string, string> = {
  충분: "coverage-badge--sufficient",
  부족: "coverage-badge--shortage",
  미가입: "coverage-badge--uninsured",
  확인필요: "coverage-badge--unknown",
};

export default function CoverageBadge({ status }: CoverageBadgeProps) {
  return <span className={`coverage-badge ${STATUS_CLASS[status] ?? "coverage-badge--unknown"}`}>{status}</span>;
}
