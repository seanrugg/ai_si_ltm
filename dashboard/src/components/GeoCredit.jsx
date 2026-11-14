// DB-IP's credit (#388): CC BY 4.0 asks for attribution wherever its data is shown or used.
// Shown only when the active city database is DB-IP's.
export default function GeoCredit({ geo, className = "" }) {
  if (!geo?.attribution) return null;
  return (
    <p className={`text-xs text-paper-muted ${className}`}>
      <a className="underline" href={geo.credit_url || "https://db-ip.com"} target="_blank" rel="noreferrer">
        IP geolocation by DB-IP
      </a>
    </p>
  );
}
