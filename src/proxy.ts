import { createServerClient } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';

const PROTECTED = ['/studio', '/konto', '/karte'];

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });
  const supabase = createServerClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (list, headers) => {
        list.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        list.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
        Object.entries(headers ?? {}).forEach(([k, v]) => response.headers.set(k, v));
      },
    },
  });

  // Sitzung erneuern und Nutzer prüfen (nichts dazwischen einfügen)
  const { data } = await supabase.auth.getUser();
  const path = request.nextUrl.pathname;

  if (!data.user && PROTECTED.some((p) => path === p || path.startsWith(p + '/'))) {
    const login = request.nextUrl.clone();
    login.pathname = '/login';
    login.search = `?weiter=${encodeURIComponent(path)}`;
    return NextResponse.redirect(login);
  }
  return response;
}

export const config = {
  // Nur App-Seiten und Auth – nicht die statische Website, Bilder, Videos oder Webhooks
  matcher: ['/studio/:path*', '/konto/:path*', '/karte/:path*', '/login', '/registrieren', '/auth/:path*'],
};
