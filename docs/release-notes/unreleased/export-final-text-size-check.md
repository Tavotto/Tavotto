## Strict export checks the actual text size in the finished PDF

With strict verification on, Tavotto now reopens the exported PDF and measures the real size of every visible piece of text — including superscripts and subscripts and panels scaled into a layout — against the minimum font size of the publication spec you chose. If anything is smaller, the export result says so in one line (for example "The smallest text in the file is 4.9 pt, below the 5 pt the spec asks for"). The export is never blocked by this, and ordinary exports without a spec are unchanged.
