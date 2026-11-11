## Dashed arrows keep a solid head

Changing an arrow's line style to dashed (or dotted) in the editor used to dash the arrowhead too,
so `->` heads broke into fragments and filled `-|>` heads got a broken outline. Now only the shaft
is dashed and the head stays solid, the way presentation tools draw it. Arrows your script itself
draws dashed are left exactly as matplotlib draws them. Styles whose shaft and head are one outline
(`simple`, `fancy`, `wedge`) are dashed as a whole, as before.
