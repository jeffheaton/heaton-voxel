# HeatonVoxel

A from-scratch toolkit for Minecraft (Java Edition) saves, for .NET and
Unity: NBT and Anvil region files, paletted chunk contents, whole-save
editing, Sponge `.schem` interchange, and the neighbor-derived block states
(fence, wall and pane connections, stair shapes) that a block update in the
game would set. It has no package dependencies, since the formats are parsed
straight from the bytes, and it targets `netstandard2.1`, so Unity can use it
directly.

It is the C# twin of the Python `heaton-voxel` library. Both are held to one
language-neutral conformance corpus, so they agree byte for byte: a save
loaded and saved again comes back identical, and an edit encodes the same in
either.

```csharp
using System;
using HeatonVoxel.World;

var save = MinecraftSave.Load("/path/to/saves/MyWorld");

var (px, _, pz) = save.PlayerPosition();
int x = (int)Math.Floor(px), z = (int)Math.Floor(pz);
int ground = save.SurfaceY(x, z) ?? throw new InvalidOperationException("no ground here");
Console.WriteLine(save.GetBlock(x, ground, z));          // e.g. minecraft:grass_block[snowy=false]

// Placed as the game places an item: both halves, connections, waterlogging.
save.PlaceBlock(x, ground + 1, z, "minecraft:oak_fence");
save.ResetLighting((x, ground, z), (x, ground + 1, z));
save.RebuildHeightmaps((x, ground, z), (x, ground + 1, z));

save.Save();   // refuses while Minecraft has the world open
```

**Scope.** It reads and writes the current chunk format (Minecraft 1.18 and
later), reads chunks saved by 1.17 and 1.17.1 but refuses to write them, and
refuses anything older with an error that says why. It reads Java Edition
worlds only: Bedrock worlds are a different format.

Documentation, the Python library and the conformance corpus are at
<https://github.com/jeffheaton/heaton-voxel>.

Licensed under the Apache License 2.0.

Not an official Minecraft product. Not approved by or associated with Mojang
or Microsoft.
