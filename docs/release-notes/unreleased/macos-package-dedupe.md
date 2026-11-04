## The macOS download and update package are smaller

**Duplicate copies of the same libraries are gone from the macOS app.** The
Python interpreter library (three copies), `libqpdf` and the image libraries
bundled with Pillow (two copies each) were stored more than once inside
`Tavotto.app`. They are now stored once and linked, so the app is about 20 MB
smaller on disk and the download and in-app update are smaller too. Nothing
the app can do changed.
