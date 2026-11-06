## Desktop journal catalog updates load correctly

The desktop app could keep showing only its built-in journals after a larger catalog was published, even after restarting. The packaged app now includes the native dependency needed to verify catalog signatures. Desktop build and smoke checks also verify a known signature and reject a tampered message, so an incomplete package fails before release.
