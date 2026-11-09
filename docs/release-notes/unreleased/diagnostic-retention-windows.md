## Clear expired diagnostics when Windows files are temporarily in use

Expired environment diagnostics are now retried when a Windows reader temporarily prevents the cache file from being replaced. If the file remains locked, Tavotto tries again when that project's diagnostics are next accessed.
