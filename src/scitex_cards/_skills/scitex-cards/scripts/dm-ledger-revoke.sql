-- Roll back ONLY the preceding reviewed grant with its recorded all-absent
-- prestate. Do not use after independent ACL changes or as a general cleanup.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '10s';
REVOKE INSERT, UPDATE ON public.status_exchanges_rows FROM scitex_cards_dm;
REVOKE INSERT ON public.status_exchanges_oplog FROM scitex_cards_dm;
REVOKE SELECT ON public.status_exchanges_rows, public.status_exchanges_oplog,
  public.status_exchanges_cursor, public.status_exchanges_identity FROM scitex_cards_dm;
COMMIT;
