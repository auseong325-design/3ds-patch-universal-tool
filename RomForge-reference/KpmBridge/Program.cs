using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Common;
using N3DS.Core.Crypto;
using N3DS.Core.Interfaces;
using N3DS.Core.Models;
using N3DS.Core.Services;
using Patch.Core;
using Patch.Core.Services;
using RomForge.Core.Models.N3DS;
using RomForge.Core.Services.N3DS;

Console.OutputEncoding = new UTF8Encoding(false);
Console.InputEncoding = new UTF8Encoding(false);
Console.SetError(new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)) { AutoFlush = true });
try
{
    string input = await Console.In.ReadToEndAsync();
    var options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
    var request = JsonSerializer.Deserialize<Request>(input, options) ?? throw new ArgumentException("요청이 비어 있습니다.");
    if (!File.Exists(request.Input)) throw new FileNotFoundException("게임 파일이 없습니다.");
    if (request.Operation == "workspace")
    {
        if (!Directory.Exists(request.KeyDirectory)) throw new DirectoryNotFoundException("키 폴더가 없습니다.");
        var keys = new KeyStore(request.KeyDirectory);
        var info = await ExtractWorkspace(request.Input, request.OutputBase, keys);
        Console.WriteLine("KPM_RESULT:" + JsonSerializer.Serialize(new { ok = true, title_id = info.TitleId, files = info.Files, code = info.Code }));
        return 0;
    }
    if (!File.Exists(request.Patch)) throw new FileNotFoundException("패치 ZIP이 없습니다.");
    if (!Directory.Exists(request.KeyDirectory)) throw new DirectoryNotFoundException("키 폴더가 없습니다.");
    if (!Enum.TryParse<RepackOutputFormat>(request.Format, true, out var format) || !Enum.IsDefined(format))
        throw new ArgumentException("출력 형식은 CCI, CIA 또는 ZCCI여야 합니다.");
    var keyStore = new KeyStore(request.KeyDirectory);
    Action<string, LogLevel> log = (message, level) => Console.Error.WriteLine($"[{level}] {message}");
    var original = await VerifyGame(request.Input, keyStore, request, false, log);
    byte[]? expectedCode = null;
    if (request.CodeIps)
    {
        if (original == null) throw new InvalidDataException("원본 ExeFS에서 .code를 찾지 못했습니다.");
        using var zip = ZipFile.OpenRead(request.Patch);
        var ips = zip.GetEntry("code.ips") ?? throw new InvalidDataException("code.ips가 패키지에 없습니다.");
        await using var stream = ips.Open(); using var buffer = new MemoryStream(); await stream.CopyToAsync(buffer);
        expectedCode = await UniversalPatcher.ApplyPatchAsync(original, buffer.ToArray(), null, CancellationToken.None);
    }
    var service = new RepackService(log, () => request.Patch);
    if (format == RepackOutputFormat.Cia)
    {
        if (request.CiaVersion.HasValue) service.CiaTitleVersion = request.CiaVersion.Value;
        else if (Path.GetExtension(request.Input).Equals(".cia", StringComparison.OrdinalIgnoreCase))
        {
            await using var cia = await new CiaReader(keyStore).OpenAsync(request.Input);
            service.CiaTitleVersion = cia.TmdHeader.TitleVersion;
        }
        else throw new InvalidDataException("CCI/3DS/ZCCI에서 CIA를 만들 때 TMD 버전 숫자를 지정해야 합니다 (본편 v1.0.0은 0).");
    }
    int lastPercent = -1;
    Action<long, long> progress = (current, total) =>
    {
        if (total <= 0) return;
        int percent = (int)Math.Clamp((double)current * 100 / total, 0, 100);
        if (percent > lastPercent) { lastPercent = percent; Console.Error.WriteLine($"KPM_PROGRESS:{percent}"); }
    };
    string output = await service.RepackDirectAsync(request.Input, request.OutputBase, keyStore, format: format, reporter: progress);
    var expected = request.Files.Select(f => f.Path.TrimStart('/')).ToHashSet(StringComparer.OrdinalIgnoreCase);
    var applied = service.LastAppliedRomfsPaths.ToHashSet(StringComparer.OrdinalIgnoreCase);
    if (!expected.SetEquals(applied))
        throw new InvalidDataException($"RomFS 적용 파일 불일치. 누락: {string.Join(", ", expected.Except(applied, StringComparer.OrdinalIgnoreCase))}");
    if ((request.CodeIps || request.ExpectedCodeSha256 != null || request.ExpectedExheaderSha256 != null || request.ExpectedBannerSha256 != null || request.ExpectedIconSha256 != null) && service.LastExeFsPatchedCount == 0) throw new InvalidDataException("코드 패치가 적용되지 않았습니다.");
    if (format == RepackOutputFormat.Cia)
    {
        await using var cia = await new CiaReader(keyStore).OpenAsync(output);
        if (cia.TmdHeader.TitleVersion != service.CiaTitleVersion)
            throw new InvalidDataException("생성한 CIA TMD 버전이 요청한 버전과 다릅니다.");
    }
    var actualCode = await VerifyGame(output, keyStore, request, true, log);
    if (expectedCode != null && (actualCode == null || !actualCode.AsSpan().SequenceEqual(expectedCode)))
        throw new InvalidDataException("리팩 결과의 코드가 기대한 IPS 적용 결과와 다릅니다.");
    Console.WriteLine("KPM_RESULT:" + JsonSerializer.Serialize(new { ok = true, output, checked_files = expected.Count, code_checked = expectedCode != null || request.ExpectedCodeSha256 != null }));
    return 0;
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex.Message);
    Console.WriteLine("KPM_RESULT:" + JsonSerializer.Serialize(new { ok = false, error = ex.Message }));
    return 1;
}

static async Task<byte[]?> VerifyGame(string path, KeyStore keys, Request request, bool result, Action<string, LogLevel> log)
{
    string ext = Path.GetExtension(path).ToLowerInvariant();
    await using INcsdSource source = ext switch
    {
        ".cia" => await new CiaReader(keys).OpenAsync(path, log),
        ".cci" or ".3ds" or ".zcci" => await CciSource.OpenAsync(path, keys, log),
        _ => throw new InvalidDataException("지원하지 않는 게임 파일 확장자입니다.")
    };
    var header = await source.GetNcchHeaderAsync(0);
    if (!string.Equals(header.ProgramId.ToString("X16"), request.TitleId, StringComparison.OrdinalIgnoreCase))
        throw new InvalidDataException($"게임의 Title ID가 다릅니다: {header.ProgramId:X16}");
    var (ncch, _) = await source.OpenContentDecrypted(0);
    await using (ncch)
    {
        var unpack = await NcchUnpacker.UnpackAsync(ncch, header);
        string? expectedExheader = result ? request.ExpectedExheaderSha256 : request.OriginalExheaderSha256;
        if (expectedExheader != null && (unpack.ExHeader == null || !string.Equals(Convert.ToHexString(SHA256.HashData(unpack.ExHeader)), expectedExheader, StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException($"{(result ? "리팩 결과" : "원본")} exheader 해시 불일치");
        // HOME Menu files in the ExeFS: the 3D banner and the SMDH icon (which carries the title text).
        foreach (var (name, label, expectedHash) in new[]
        {
            ("banner", "배너", result ? request.ExpectedBannerSha256 : request.OriginalBannerSha256),
            ("icon", "아이콘(게임 이름)", result ? request.ExpectedIconSha256 : request.OriginalIconSha256),
        })
        {
            if (expectedHash == null) continue;
            byte[]? data = unpack.ExeFs?.Files.FirstOrDefault(f => f.Name == name)?.Data;
            if (data == null || !string.Equals(Convert.ToHexString(SHA256.HashData(data)), expectedHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException($"{(result ? "리팩 결과" : "원본")} {label} 해시 불일치");
        }
        if (request.Files.Count > 0 && unpack.RomFs == null) throw new InvalidDataException("게임에 RomFS가 없습니다.");
        foreach (var entry in request.Files)
        {
            var matches = unpack.RomFs!.Files.Where(f => string.Equals(f.FullPath.TrimStart('/'), entry.Path, StringComparison.OrdinalIgnoreCase)).ToList();
            if (matches.Count != 1) throw new InvalidDataException($"게임에서 원본 파일을 찾을 수 없거나 중복됩니다: {entry.Path}");
            var file = matches[0];
            ncch.Position = checked(unpack.RomFs.DataLevel2Offset + unpack.RomFs.RomFsHeader.DataOffset + (long)file.DataOffset);
            using var digest = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            long remaining = checked((long)file.DataSize); byte[] buf = new byte[1024 * 128];
            while (remaining > 0)
            {
                int count = await ncch.ReadAsync(buf.AsMemory(0, (int)Math.Min(remaining, buf.Length)));
                if (count == 0) throw new EndOfStreamException($"파일 데이터가 잘렸습니다: {entry.Path}");
                digest.AppendData(buf, 0, count); remaining -= count;
            }
            string actual = Convert.ToHexString(digest.GetHashAndReset());
            string expectedHash = result ? entry.ModifiedSha256 : entry.OriginalSha256;
            if (!string.Equals(actual, expectedHash, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException($"{(result ? "리팩 결과" : "게임 원본")} 파일 해시 불일치: {entry.Path}");
        }
        if (!request.CodeIps && request.ExpectedCodeSha256 == null) return null;
        var code = unpack.ExeFs?.Files.FirstOrDefault(f => f.Name == ".code")?.Data
            ?? throw new InvalidDataException("게임에서 .code 파일을 찾을 수 없습니다.");
        if (unpack.ExHeader is { Length: > 13 } && (unpack.ExHeader[13] & 1) != 0) code = BackwardLz77.Decompress(code);
        if (!result && !string.IsNullOrWhiteSpace(request.OriginalCodeSha256)
            && !string.Equals(Convert.ToHexString(SHA256.HashData(code)), request.OriginalCodeSha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("게임의 원본 코드 해시가 선택한 코드 파일과 다릅니다.");
        if (result && request.ExpectedCodeSha256 != null && !string.Equals(Convert.ToHexString(SHA256.HashData(code)), request.ExpectedCodeSha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("리팩 결과 코드 해시 불일치");
        return code;
    }
}

static async Task<(string TitleId, int Files, bool Code)> ExtractWorkspace(string input, string directory, KeyStore keys)
{
    string ext = Path.GetExtension(input).ToLowerInvariant();
    await using INcsdSource source = ext switch
    {
        ".cia" => await new CiaReader(keys).OpenAsync(input),
        ".cci" or ".3ds" or ".zcci" => await CciSource.OpenAsync(input, keys),
        _ => throw new InvalidDataException("지원하지 않는 게임 파일 확장자입니다.")
    };
    var header = await source.GetNcchHeaderAsync(0);
    var (stream, _) = await source.OpenContentDecrypted(0);
    await using (stream)
    {
        var unpack = await NcchUnpacker.UnpackAsync(stream, header);
        if (unpack.RomFs == null) throw new InvalidDataException("게임에 RomFS가 없습니다.");
        string root = Path.GetFullPath(Path.Combine(directory, "original"));
        Directory.CreateDirectory(root);
        foreach (var file in unpack.RomFs.Files)
        {
            string relative = file.FullPath.TrimStart('/');
            string[] parts = relative.Split('/');
            if (parts.Length == 0 || parts.Any(p => string.IsNullOrEmpty(p) || p is "." or ".." || p.EndsWith(' ') || p.EndsWith('.') || p.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || p.Contains('\\') || IsWindowsDeviceName(p)))
                throw new InvalidDataException($"지원하지 않는 RomFS 경로: {file.FullPath}");
            string dest = Path.GetFullPath(Path.Combine(root, Path.Combine(parts)));
            if (!dest.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("RomFS 경로가 출력 폴더를 벗어납니다.");
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            stream.Position = checked(unpack.RomFs.DataLevel2Offset + unpack.RomFs.RomFsHeader.DataOffset + (long)file.DataOffset);
            long remaining = checked((long)file.DataSize); byte[] buf = new byte[128 * 1024];
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            await using (var output = new FileStream(dest, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                while (remaining > 0)
                {
                    int count = await stream.ReadAsync(buf.AsMemory(0, (int)Math.Min(remaining, buf.Length)));
                    if (count == 0) throw new EndOfStreamException($"RomFS 데이터가 잘렸습니다: {file.FullPath}");
                    hash.AppendData(buf, 0, count); await output.WriteAsync(buf.AsMemory(0, count)); remaining -= count;
                }
            }
            string expected = Convert.ToHexString(hash.GetHashAndReset());
            await using var check = File.OpenRead(dest);
            if (!string.Equals(expected, Convert.ToHexString(await SHA256.HashDataAsync(check)), StringComparison.Ordinal))
                throw new InvalidDataException($"추출 파일 해시 불일치: {file.FullPath}");
        }
        if (unpack.ExHeader != null) await File.WriteAllBytesAsync(Path.Combine(directory, "original_exheader.bin"), unpack.ExHeader);
        foreach (string name in new[] { "banner", "icon" })
        {
            byte[]? data = unpack.ExeFs?.Files.FirstOrDefault(f => f.Name == name)?.Data;
            if (data != null) await File.WriteAllBytesAsync(Path.Combine(directory, $"original_{name}.bin"), data);
        }
        byte[]? code = unpack.ExeFs?.Files.FirstOrDefault(f => f.Name == ".code")?.Data;
        if (code != null)
        {
            if (unpack.ExHeader is { Length: > 13 } && (unpack.ExHeader[13] & 1) != 0) code = BackwardLz77.Decompress(code);
            await File.WriteAllBytesAsync(Path.Combine(directory, "original_code.bin"), code);
        }
        return (header.ProgramId.ToString("X16"), unpack.RomFs.Files.Count, code != null);
    }
}

static bool IsWindowsDeviceName(string name)
{
    string stem = name.Split('.')[0].ToUpperInvariant();
    return stem is "CON" or "PRN" or "AUX" or "NUL" ||
        (stem.Length == 4 && (stem.StartsWith("COM") || stem.StartsWith("LPT")) && stem[3] is >= '1' and <= '9');
}

internal sealed class Request
{
    public string Operation { get; set; } = "repack";
    public string Input { get; set; } = "";
    public string Patch { get; set; } = "";
    public string OutputBase { get; set; } = "";
    public string KeyDirectory { get; set; } = "";
    public string Format { get; set; } = "";
    public string TitleId { get; set; } = "";
    public bool CodeIps { get; set; }
    public ushort? CiaVersion { get; set; }
    public string? OriginalCodeSha256 { get; set; }
    public string? ExpectedCodeSha256 { get; set; }
    public string? OriginalExheaderSha256 { get; set; }
    public string? ExpectedExheaderSha256 { get; set; }
    public string? OriginalBannerSha256 { get; set; }
    public string? ExpectedBannerSha256 { get; set; }
    public string? OriginalIconSha256 { get; set; }
    public string? ExpectedIconSha256 { get; set; }
    public List<PatchFile> Files { get; set; } = [];
}
internal sealed class PatchFile
{
    public string Path { get; set; } = "";
    public string OriginalSha256 { get; set; } = "";
    public string ModifiedSha256 { get; set; } = "";
}
