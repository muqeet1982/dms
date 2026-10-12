import { QueryClient } from "@tanstack/react-query";

/**
 * The one query cache, exported so that signing in or changing company can empty it.
 *
 * Almost none of the 260-odd query keys in this app name the company — they are `["customers"]`, not
 * `["customers", tenantId]` — because every request carries the company in a header instead. That is fine until
 * the company *changes*: the cache then answers from the company the user has just left, and an id picked out of
 * a stale list belongs to a company the server will not look in, which comes back as `Invalid pk "…" - object
 * does not exist` on a form that looks perfectly filled in. Emptying the cache at that one moment is far safer
 * than trusting every key to remember.
 */
export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 } },
});
