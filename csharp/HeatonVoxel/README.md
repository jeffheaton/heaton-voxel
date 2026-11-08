# HeatonVoxel

[![NuGet](https://img.shields.io/nuget/v/HeatonVoxel?style=flat-square)](https://www.nuget.org/packages/HeatonVoxel)
[![.NET tests](https://img.shields.io/github/actions/workflow/status/jeffheaton/heaton-voxel/test-lib-dotnet.yml?branch=main&label=.NET%20tests&style=flat-square)](https://github.com/jeffheaton/heaton-voxel/actions/workflows/test-lib-dotnet.yml)
[![.NET coverage](https://img.shields.io/codecov/c/github/jeffheaton/heaton-voxel/main?flag=dotnet&label=.NET%20coverage&style=flat-square)](https://app.codecov.io/gh/jeffheaton/heaton-voxel)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](https://github.com/jeffheaton/heaton-voxel/blob/main/LICENSE)

Read and edit Minecraft Java Edition worlds from .NET and Unity.

Open a save, read any block, change it, and write the world back the way the
game would have made it: a door placed as two halves, a fence joined to its
neighbors, a stair set in water waterlogged. Move builds in and out as
WorldEdit `.schem`, Litematica `.litematic` and vanilla structure files,
change the game mode, difficulty, weather and time of day, and undo an edit.
Everything you didn't touch is written back byte for byte, and saving refuses
while Minecraft has the world open. It has no package dependencies and
targets `netstandard2.1`, so Unity can use it directly.

It is the C# twin of the Python `heaton-voxel` library. One shared test
corpus holds them to the same answers, so a save edited in either comes out
identical, byte for byte.

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
later), reads chunks saved by 1.13 to 1.17.1 but refuses to write them, and
refuses anything older with an error that says why. It reads Java Edition
worlds only: Bedrock worlds are a different format.

Documentation, the Python library and the conformance corpus are at
<https://github.com/jeffheaton/heaton-voxel>.

Licensed under the Apache License 2.0.

Not an official Minecraft product. Not approved by or associated with Mojang
or Microsoft.
