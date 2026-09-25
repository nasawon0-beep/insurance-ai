import React from "react";

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: string;
  trend?: {
    value: number;
    label: string;
  };
}

export function StatCard({ title, value, subtitle, icon, trend }: StatCardProps) {
  return (
    <div className="card card-hover animate-fade-in">
      <div className="card-body" style={{ textAlign: "center", padding: "var(--space-6)" }}>
        {icon && (
          <div style={{ fontSize: "2rem", marginBottom: "8px" }}>
            {icon}
          </div>
        )}
        <div className="text-sm text-secondary" style={{ fontSize: "0.875rem", marginBottom: "var(--space-1)" }}>
          {title}
        </div>
        <div className="text-3xl font-bold" style={{ fontSize: "2rem", fontWeight: 700, margin: "var(--space-2) 0" }}>
          {value}
        </div>
        {subtitle && (
          <div className="text-sm text-secondary" style={{ fontSize: "0.875rem" }}>
            {subtitle}
          </div>
        )}
        {trend && (
          <div
            className="text-sm font-medium"
            style={{
              marginTop: "var(--space-2)",
              fontSize: "0.875rem",
              color: trend.value >= 0 ? "var(--color-success)" : "var(--color-error)",
            }}
          >
            {trend.value >= 0 ? "↑" : "↓"} {Math.abs(trend.value)} {trend.label}
          </div>
        )}
      </div>
    </div>
  );
}

interface DashboardCardProps {
  title: string;
  count: number;
  children: React.ReactNode;
  icon?: string;
}

export function DashboardCard({ title, count, children, icon }: DashboardCardProps) {
  return (
    <div className="card animate-slide-up">
      <div className="card-header">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {icon && <span style={{ fontSize: "1.25rem" }}>{icon}</span>}
            <h3 className="card-title">{title}</h3>
          </div>
          {count > 0 && (
            <span className="badge badge-primary-solid">
              {count}
            </span>
          )}
        </div>
      </div>
      <div className="card-body" style={{ padding: "0" }}>
        {count === 0 ? (
          <div style={{ padding: "var(--space-6)", textAlign: "center", color: "var(--color-text-secondary)" }}>
            항목이 없습니다.
          </div>
        ) : (
          <div>{children}</div>
        )}
      </div>
    </div>
  );
}
