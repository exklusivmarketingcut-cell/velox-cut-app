import { createServerClient } from '@supabase/ssr';
import { createClient as createPlainClient } from '@supabase/supabase-js';
import { cookies } from 'next/headers';

const url = () => process.env.NEXT_PUBLIC_SUPABASE_URL!;

/** Supabase mit der Sitzung des eingeloggten Nutzers (für Server Components und Route Handlers). */
export async function createClient() {
  const store = await cookies();
  return createServerClient(url(), process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!, {
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list) => {
        try {
          list.forEach(({ name, value, options }) => store.set(name, value, options));
        } catch {
          // In Server Components nicht erlaubt – der Proxy erneuert die Sitzung.
        }
      },
    },
  });
}

/** Voller Zugriff (service_role) – NUR auf dem Server verwenden, nie an den Browser geben. */
export function createAdmin() {
  return createPlainClient(url(), process.env.SUPABASE_SERVICE_ROLE_KEY!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

/** Eingeloggten Nutzer sicher ermitteln (Token wird geprüft). */
export async function getUser() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  return data.user ?? null;
}
