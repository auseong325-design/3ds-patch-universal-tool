using System.Buffers.Binary;
using N3DS.Core.Models;
using N3DS.Core.Services;
using N3DS.Core.Interfaces;

if (args.Length != 1) throw new ArgumentException("Output directory is required");
string root = Path.GetFullPath(args[0]);
Directory.CreateDirectory(root);
string orig = Path.Combine(root, "original"); string edit = Path.Combine(root, "edited"); string keys = Path.Combine(root, "keys"); string upd = Path.Combine(root, "update");
Directory.CreateDirectory(orig); Directory.CreateDirectory(edit); Directory.CreateDirectory(keys); Directory.CreateDirectory(upd);
await File.WriteAllTextAsync(Path.Combine(orig, "test.bin"), "original-data");
await File.WriteAllTextAsync(Path.Combine(edit, "test.bin"), "translated-data");
await File.WriteAllTextAsync(Path.Combine(upd, "test.bin"), "updated-data");
await File.WriteAllTextAsync(Path.Combine(keys, "aes_keys.txt"), "common0=00000000000000000000000000000000\n");
await File.WriteAllBytesAsync(Path.Combine(keys, "certs.bin"), new byte[0x100]);
byte[] code = Enumerable.Range(0, 4096).Select(i => (byte)i).ToArray();
await File.WriteAllBytesAsync(Path.Combine(root, "code.bin"), code);
await File.WriteAllBytesAsync(Path.Combine(root, "code.ips"), [.. "PATCH"u8.ToArray(), 0,0,4,0,2,0xAA,0xBB, .. "EOF"u8.ToArray()]);
await File.WriteAllBytesAsync(Path.Combine(root, "banner_ko.bin"), "new banner"u8.ToArray());
ulong baseTitle = 0x0004000000123456;
await Create(Path.Combine(root,"game.cci"),orig,baseTitle,baseTitle,code,false);
byte[] updateCode=(byte[])code.Clone(); updateCode[20]=0xAB;
await Create(Path.Combine(root,"update.cci"),upd,0x0004000E00123456,baseTitle,updateCode,true);
Console.WriteLine("Fixture created");

static async Task Create(string outputPath, string original, ulong title, ulong baseTitle, byte[] code, bool update)
{
    var header = new NcchHeader { ProgramId = title, PartitionId = title, ExtendedHeaderSize = 0x400 };
    var fs = new RomFsUnpackResult
    {
        IvfcHeader = new IvfcHeader(), RomFsHeader = new RomFsHeader(), DataLevel2Offset = 0,
        Directories = [new RomFsDirNode { FullPath = "/", Entry = new RomFsDirEntry { Name = "" } }],
        Files = [new RomFsFileNode { FullPath = "/test.bin", DataOffset = 0, DataSize = (ulong)new FileInfo(Path.Combine(original,"test.bin")).Length, Entry = new RomFsFileEntry { Name = "test.bin" } }],
    };
    var exefs = ExeFsPacker.Pack([
        new ExeFsFile { Name = ".code", Data = code, ExpectedHash = [], HashValid = false },
        new ExeFsFile { Name = "banner", Data = "old banner"u8.ToArray(), ExpectedHash = [], HashValid = false }
    ]);
    byte[] exheader = new byte[0x800];
    BinaryPrimitives.WriteUInt32LittleEndian(exheader.AsSpan(0x14), 1);
    BinaryPrimitives.WriteUInt32LittleEndian(exheader.AsSpan(0x18), update ? 80u : 64u);
    BinaryPrimitives.WriteUInt16LittleEndian(exheader.AsSpan(0x0e), update ? (ushort)2 : (ushort)0);
    BinaryPrimitives.WriteUInt64LittleEndian(exheader.AsSpan(0x200), baseTitle);
    BinaryPrimitives.WriteUInt64LittleEndian(exheader.AsSpan(0x1c8), title);
    var unpack = new NcchUnpackResult { Header = header, ExHeader = exheader, RomFs = fs };
    var source = new FolderRomFsFileSource(original);
    var dict = new Dictionary<int, (NcchUnpackResult, byte[], Stream, RomFsUnpackResult?, IRomFsFileSource?)> { [0] = (unpack, exefs, Stream.Null, fs, source) };
    var packed = await RepackedNcsdSource.CreateAsync(dict, [new Contents { ContentIndex = 0 }]);
    await using var output = File.Open(outputPath, FileMode.CreateNew, FileAccess.ReadWrite);
    await NcsdBuilder.BuildAsync(packed, output);
}
