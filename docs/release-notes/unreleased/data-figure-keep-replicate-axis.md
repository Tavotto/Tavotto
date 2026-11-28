## Data-to-figure keeps a block or batch axis when AI would otherwise leave only a histogram

- With an AI connected, when the column picked for the horizontal axis is a block or batch number and nothing else in the table could group the data, the suggested figure now keeps that axis instead of collapsing to a single histogram. When the table also has a treatment or group column to compare by, the block or batch number is still dropped from the axis as before.
