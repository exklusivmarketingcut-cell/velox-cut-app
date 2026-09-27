import Link from 'next/link';

export default function AuthShell({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <main className="auth">
      <div className="card">
        <Link href="/" aria-label="Zur Startseite">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="logo" src="/assets/img/logo-full-white.png" alt="Velox Cut" />
        </Link>
        <h1>{title}</h1>
        {sub ? <p className="sub">{sub}</p> : null}
        {children}
      </div>
    </main>
  );
}
