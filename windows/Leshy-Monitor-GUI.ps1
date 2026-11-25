param(
    [string]$Repository,
    [string]$BrainRoot,
    [ValidateRange(2,30)][int]$Interval = 2
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
if (-not $Repository) { $Repository = Split-Path -Parent $PSScriptRoot }
$repo=[IO.Path]::GetFullPath($Repository)
if (-not $BrainRoot) { $BrainRoot=Join-Path $repo 'dist\Leshy\LeshyBrain' }
$brain=[IO.Path]::GetFullPath($BrainRoot)
$collector=Join-Path $PSScriptRoot 'Leshy-Monitor.ps1'
$script:process=$null
$script:readTask=$null
$script:sample=$null
$script:lastReceived=$null
$script:history=New-Object 'System.Collections.Generic.List[object]'
$script:section='CPU'
$script:gpuKey=$null
$script:updatingGpu=$false
$script:failure=''
$script:retryAfter=[datetime]::MinValue
$script:collectorStarted=$null

[xml]$xaml=@'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
 xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
 Title="Леший — монитор производительности" Width="1120" Height="760" MinWidth="820" MinHeight="620"
 WindowStartupLocation="CenterScreen" Background="#11161E" Foreground="#F0F4F8"
 FontFamily="Segoe UI" FontSize="14" UseLayoutRounding="True">
 <Window.Resources>
  <Style TargetType="TextBlock"><Setter Property="Foreground" Value="#F0F4F8"/></Style>
  <Style x:Key="Muted" TargetType="TextBlock"><Setter Property="Foreground" Value="#A9B6C6"/><Setter Property="FontSize" Value="12"/><Setter Property="TextWrapping" Value="Wrap"/></Style>
  <Style TargetType="Button">
   <Setter Property="Background" Value="#202B39"/><Setter Property="Foreground" Value="#F0F4F8"/>
   <Setter Property="Padding" Value="12,7"/><Setter Property="BorderBrush" Value="#34445A"/>
   <Setter Property="Cursor" Value="Hand"/>
  </Style>
  <Style TargetType="ComboBox"><Setter Property="MinWidth" Value="155"/><Setter Property="MaxWidth" Value="350"/><Setter Property="Margin" Value="8,0,0,0"/></Style>
 </Window.Resources>
 <Grid>
  <Grid.RowDefinitions><RowDefinition Height="62"/><RowDefinition Height="*"/><RowDefinition Height="42"/></Grid.RowDefinitions>
  <Border Grid.Row="0" Background="#181F29" BorderBrush="#2A3545" BorderThickness="0,0,0,1">
   <DockPanel Margin="22,0">
    <StackPanel DockPanel.Dock="Right" VerticalAlignment="Center" HorizontalAlignment="Right">
     <TextBlock x:Name="LiveState" Text="Подключение к счётчикам…" Foreground="#7CD8AB" HorizontalAlignment="Right"/>
     <TextBlock x:Name="UpdatedAt" Text="Ожидается первый замер" Style="{StaticResource Muted}" HorizontalAlignment="Right"/>
    </StackPanel>
    <StackPanel VerticalAlignment="Center">
     <TextBlock Text="Леший" FontWeight="SemiBold" FontSize="21"/>
     <TextBlock Text="Монитор производительности" Style="{StaticResource Muted}"/>
    </StackPanel>
   </DockPanel>
  </Border>
  <Grid Grid.Row="1">
   <Grid.ColumnDefinitions><ColumnDefinition Width="244"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
   <Border Background="#151C25" BorderBrush="#293646" BorderThickness="0,0,1,0">
    <ScrollViewer VerticalScrollBarVisibility="Auto">
     <StackPanel Margin="12,16,12,16">
      <TextBlock Text="ПРОИЗВОДИТЕЛЬНОСТЬ" Foreground="#91A0B3" FontSize="11" Margin="8,0,0,14"/>
      <StackPanel x:Name="Navigation"/>
      <Border Background="#1A2532" CornerRadius="7" Padding="12" Margin="0,16,0,0">
       <StackPanel><TextBlock Text="Обновлятор" Foreground="#A9B6C6" FontSize="12"/>
        <TextBlock x:Name="BuildState" FontSize="14" FontWeight="SemiBold" TextWrapping="Wrap" Margin="0,5,0,4"/>
        <TextBlock x:Name="BuildStage" Style="{StaticResource Muted}"/>
       </StackPanel>
      </Border>
     </StackPanel>
    </ScrollViewer>
   </Border>
   <Grid Grid.Column="1" Margin="22,20,22,10">
    <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
    <DockPanel Margin="0,0,0,14">
     <ComboBox x:Name="GpuSelect" DockPanel.Dock="Right" VerticalAlignment="Center" Visibility="Collapsed"/>
     <StackPanel>
      <TextBlock x:Name="Heading" FontSize="27" FontWeight="SemiBold"/>
      <TextBlock x:Name="Subheading" Style="{StaticResource Muted}" Margin="0,5,0,0"/>
     </StackPanel>
    </DockPanel>
    <Border Grid.Row="1" CornerRadius="8" BorderThickness="1" BorderBrush="#314256" Background="#131C27" Padding="12">
     <Grid>
      <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <DockPanel Margin="0,0,0,8">
       <TextBlock x:Name="ChartMax" DockPanel.Dock="Right" Text="100%" Style="{StaticResource Muted}"/>
       <TextBlock x:Name="ChartName" FontWeight="SemiBold" FontSize="13"/>
      </DockPanel>
      <Canvas x:Name="Plot" Grid.Row="1" ClipToBounds="True" Background="#101822" MinHeight="100"/>
      <DockPanel Grid.Row="2" Margin="0,8,0,0">
       <TextBlock Text="Сейчас" DockPanel.Dock="Right" Style="{StaticResource Muted}"/>
       <TextBlock Text="60 секунд назад" Style="{StaticResource Muted}"/>
      </DockPanel>
     </Grid>
    </Border>
    <UniformGrid x:Name="MetricCards" Grid.Row="2" Columns="3" Margin="-4,10,-4,0"/>
    <Border Grid.Row="3" CornerRadius="7" Background="#181F29" Padding="12,9" Margin="0,12,0,0">
     <TextBlock x:Name="Details" FontSize="12" Foreground="#B7C6D8" TextWrapping="Wrap" MinHeight="42"/>
    </Border>
   </Grid>
  </Grid>
  <Border Grid.Row="2" BorderBrush="#293646" BorderThickness="0,1,0,0" Background="#181F29">
   <DockPanel Margin="16,0">
    <Button x:Name="ConsoleButton" DockPanel.Dock="Right" Content="Технический режим" VerticalAlignment="Center" Padding="10,4"/>
    <TextBlock x:Name="Footer" Text="Только чтение • Закрытие окна не останавливает Лешего" Style="{StaticResource Muted}" VerticalAlignment="Center" Margin="0,0,10,0"/>
   </DockPanel>
  </Border>
 </Grid>
</Window>
'@
$reader=New-Object System.Xml.XmlNodeReader $xaml
$window=[System.Windows.Markup.XamlReader]::Load($reader)
$ui=@{}
foreach ($n in @('LiveState','UpdatedAt','Navigation','BuildState','BuildStage','GpuSelect','Heading','Subheading','ChartMax','ChartName','Plot','MetricCards','Details','ConsoleButton','Footer')) {$ui[$n]=$window.FindName($n)}
function Brush([string]$hex) { return ([System.Windows.Media.BrushConverter]::new()).ConvertFromString($hex) }
function FormatValue($v,[string]$format='N1',[string]$suffix='') { if ($null -eq $v) {return '—'};return ([double]$v).ToString($format)+$suffix }
function Percent($v) { return (FormatValue $v 'N0' '%') }
function NewText([string]$value,[double]$size=14,[string]$color='#F0F4F8') {
    $t=[System.Windows.Controls.TextBlock]::new();$t.Text=$value;$t.FontSize=$size;$t.Foreground=Brush $color;$t.TextWrapping='Wrap';return $t
}
$sections=@(
    @{id='CPU';title='Процессор';color='#4AA5F0';key='cpu'},
    @{id='RAM';title='Память';color='#A78BFA';key='ram_percent'},
    @{id='Disk';title='Диск';color='#69C58C';key='disk'},
    @{id='GPU';title='Видеокарта';color='#46C9BC';key='gpu'},
    @{id='AI';title='Леший';color='#F4BB62';key='rate'}
)
$nav=@{}
foreach ($s in $sections) {
    $button=[System.Windows.Controls.Button]::new();$button.Tag=$s.id;$button.Margin=[System.Windows.Thickness]::new(0,0,0,7);$button.Padding=[System.Windows.Thickness]::new(10,9,10,9);$button.HorizontalContentAlignment='Stretch'
    $row=[System.Windows.Controls.DockPanel]::new()
    $stripe=[System.Windows.Controls.Border]::new();$stripe.Width=3;$stripe.Background=Brush $s.color;$stripe.CornerRadius=[System.Windows.CornerRadius]::new(2);$stripe.Margin=[System.Windows.Thickness]::new(0,0,10,0);[System.Windows.Controls.DockPanel]::SetDock($stripe,'Left');[void]$row.Children.Add($stripe)
    $mini=[System.Windows.Controls.Canvas]::new()
    $mini.Width=55;$mini.Height=36;$mini.ClipToBounds=$true
    $mini.Background=Brush '#131C27';$mini.Margin=[System.Windows.Thickness]::new(0,0,8,0)
    [System.Windows.Controls.DockPanel]::SetDock($mini,'Left');[void]$row.Children.Add($mini)
    $stack=[System.Windows.Controls.StackPanel]::new()
    $title=NewText $s.title 14;$title.FontWeight='SemiBold'
    $value=NewText '—' 13 '#A9B6C6';$value.Margin=[System.Windows.Thickness]::new(0,4,0,0)
    [void]$stack.Children.Add($title);[void]$stack.Children.Add($value);[void]$row.Children.Add($stack);$button.Content=$row
    $button.Add_Click({param($sender,$e) $script:section=[string]$sender.Tag;RenderMonitor})
    [void]$ui.Navigation.Children.Add($button);$nav[$s.id]=@{button=$button;value=$value;mini=$mini}
}
$cards=@()
for ($i=0;$i -lt 6;$i++) {
    $border=[System.Windows.Controls.Border]::new();$border.Background=Brush '#1B2633';$border.CornerRadius=[System.Windows.CornerRadius]::new(7);$border.Padding=[System.Windows.Thickness]::new(11);$border.Margin=[System.Windows.Thickness]::new(4);$border.MinHeight=66
    $stack=[System.Windows.Controls.StackPanel]::new()
    $label=NewText '—' 12 '#A9B6C6';$val=NewText '—' 21;$val.FontWeight='SemiBold';$val.Margin=[System.Windows.Thickness]::new(0,5,0,0)
    [void]$stack.Children.Add($label);[void]$stack.Children.Add($val);$border.Child=$stack
    [void]$ui.MetricCards.Children.Add($border);$cards+=@{border=$border;label=$label;value=$val}
}
function ShowCards([object[]]$pairs) {
    $count=[int]($pairs.Count/2)
    $ui.MetricCards.Columns=[Math]::Max(1,[Math]::Min(3,$count))
    for($i=0;$i -lt $cards.Count;$i++) {
        $c=$cards[$i]
        if($i -lt $count) {$c.label.Text=[string]$pairs[2*$i];$c.value.Text=[string]$pairs[2*$i+1];$c.border.Visibility='Visible'}
        else {$c.border.Visibility='Collapsed'}
    }
}
function SelectedGpu($s) {
    if($null -eq $s) {return $null}
    return ($s.gpus | Where-Object { $_.index -eq $script:gpuKey } | Select-Object -First 1)
}
function GpuMemoryPercent($g) {if($g -and $null -ne $g.used -and $null -ne $g.total -and $g.total -gt 0){return 100.0*$g.used/$g.total};return $null}
function SampleValue($s,$key) {
    if($null -eq $s){return $null}
    if($key -eq 'gpu'){ $g=SelectedGpu $s;return $g.load }
    if($key -eq 'vram'){return (GpuMemoryPercent (SelectedGpu $s))}
    return $s.$key
}
function DrawLine($x1,$y1,$x2,$y2,$color,$thickness=1) {
    $l=[System.Windows.Shapes.Line]::new();$l.X1=$x1;$l.Y1=$y1;$l.X2=$x2;$l.Y2=$y2;$l.Stroke=Brush $color;$l.StrokeThickness=$thickness;[void]$ui.Plot.Children.Add($l)
}
function DrawSeries($key,$color,$scale,$end,$w,$h) {
    $previous=$null
    foreach($s in $script:history) {
        try {$age=($end-[datetimeoffset]::Parse([string]$s.timestamp)).TotalSeconds} catch {$previous=$null;continue}
        $v=SampleValue $s $key
        if($age -lt 0 -or $age -gt 60 -or $null -eq $v){$previous=$null;continue}
        $x=$w*(1-$age/60);$y=$h*(1-[Math]::Max(0,[Math]::Min(1,[double]$v/$scale)))
        if($null -ne $previous -and $previous.age-$age -le 10) {DrawLine $previous.x $previous.y $x $y $color 2}
        $dot=[System.Windows.Shapes.Ellipse]::new();$dot.Width=4;$dot.Height=4;$dot.Fill=Brush $color;[System.Windows.Controls.Canvas]::SetLeft($dot,$x-2);[System.Windows.Controls.Canvas]::SetTop($dot,$y-2);[void]$ui.Plot.Children.Add($dot)
        $previous=@{x=$x;y=$y;age=$age}
    }
}
function DrawMiniPlot($sec) {
    $canvas=$nav[$sec.id].mini
    $canvas.Children.Clear()
    $w=$canvas.Width;$h=$canvas.Height
    $scale=100.0;$end=[datetimeoffset]::UtcNow
    if($sec.id -eq 'AI'){
        $scale=10.0
        foreach($sample in $script:history){if($null -ne $sample.rate){$scale=[Math]::Max($scale,[double]$sample.rate)}}
    }
    $previous=$null
    foreach($sample in $script:history){
        try{$age=($end-[datetimeoffset]::Parse([string]$sample.timestamp)).TotalSeconds}catch{$previous=$null;continue}
        $v=SampleValue $sample $sec.key
        if($age -lt 0 -or $age -gt 60 -or $null -eq $v){$previous=$null;continue}
        $x=1+($w-2)*(1-$age/60);$y=1+($h-2)*(1-[Math]::Max(0,[Math]::Min(1,[double]$v/$scale)))
        if($null -ne $previous -and $previous.age-$age -le 10){
            $line=[System.Windows.Shapes.Line]::new()
            $line.X1=$previous.x;$line.Y1=$previous.y;$line.X2=$x;$line.Y2=$y
            $line.Stroke=Brush $sec.color;$line.StrokeThickness=1.4
            [void]$canvas.Children.Add($line)
        }else{
            $dot=[System.Windows.Shapes.Ellipse]::new();$dot.Width=3;$dot.Height=3;$dot.Fill=Brush $sec.color
            [System.Windows.Controls.Canvas]::SetLeft($dot,$x-1.5);[System.Windows.Controls.Canvas]::SetTop($dot,$y-1.5)
            [void]$canvas.Children.Add($dot)
        }
        $previous=@{x=$x;y=$y;age=$age}
    }
}
function DrawPlot {
    $ui.Plot.Children.Clear()
    $w=$ui.Plot.ActualWidth;$h=$ui.Plot.ActualHeight
    if($w -lt 10 -or $h -lt 10){return}
    $sec=@($sections | Where-Object {$_.id -eq $script:section})[0]
    $end=[datetimeoffset]::UtcNow;$scale=100
    if($sec.id -eq 'AI'){
        $max=0.0;foreach($s in $script:history){if($null -ne $s.rate){$max=[Math]::Max($max,[double]$s.rate)}}
        $scale=[Math]::Max(10,10*[Math]::Ceiling($max/10));$ui.ChartMax.Text=([string]$scale)+' ток/с'
    }else{$ui.ChartMax.Text='100%'}
    for($i=0;$i -le 6;$i++){DrawLine ($w*$i/6) 0 ($w*$i/6) $h '#233245'}
    for($i=0;$i -le 4;$i++){DrawLine 0 ($h*$i/4) $w ($h*$i/4) '#233245'}
    DrawSeries $sec.key $sec.color $scale $end $w $h
    if($sec.id -eq 'GPU'){DrawSeries 'vram' '#F4BB62' 100 $end $w $h}
}
function RenderMonitor {
    $s=$script:sample
    foreach($sec in $sections){
        $nav[$sec.id].button.Background=Brush $(if($sec.id -eq $script:section){'#283B51'}else{'#1A2532'})
        $v=SampleValue $s $sec.key
        $nav[$sec.id].value.Text=$(if($sec.id -eq 'AI'){FormatValue $v 'N1' ' ток/с'}else{Percent $v})
        DrawMiniPlot $sec
    }
    $ui.GpuSelect.Visibility=$(if($script:section -eq 'GPU'){'Visible'}else{'Collapsed'})
    $g=SelectedGpu $s
    $ui.Details.Text='Ожидается подтверждённый замер. Недоступные счётчики отображаются прочерком.'
    switch($script:section) {
      'CPU' {
        $ui.Heading.Text='Процессор';$ui.Subheading.Text='Общая загрузка системы, включая сборку и фоновые процессы';$ui.ChartName.Text='Загрузка CPU'
        ShowCards @('Загрузка',(Percent $s.cpu))
      }
      'RAM' {
        $ui.Heading.Text='Оперативная память';$ui.Subheading.Text='Физическая память системы';$ui.ChartName.Text='Использование RAM'
        $used=$null;if($null -ne $s.ram_total_gib -and $null -ne $s.ram_free_gib){$used=$s.ram_total_gib-$s.ram_free_gib}
        ShowCards @('Загрузка',(Percent $s.ram_percent),'Используется',(FormatValue $used 'N1' ' ГБ'),'Доступно',(FormatValue $s.ram_free_gib 'N1' ' ГБ'),'Всего',(FormatValue $s.ram_total_gib 'N1' ' ГБ'))
      }
      'Disk' {
        $ui.Heading.Text='Диск';$ui.Subheading.Text='Общая активность физических дисков';$ui.ChartName.Text='Активное время'
        ShowCards @('Активность',(Percent $s.disk),'Передача данных',(FormatValue $s.disk_mib_s 'N1' ' МиБ/с'))
      }
      'GPU' {
        $ui.Heading.Text='Видеокарта';$ui.Subheading.Text=$(if($g){$g.name}else{'Счётчики NVIDIA недоступны'});$ui.ChartName.Text='GPU (бирюзовый) · VRAM (жёлтый)'
        $used=$null;$total=$null;if($null -ne $g.used){$used=$g.used/1024};if($null -ne $g.total){$total=$g.total/1024}
        ShowCards @('GPU',(Percent $g.load),'VRAM',(Percent (GpuMemoryPercent $g)),'Температура',(FormatValue $g.temperature 'N0' ' °C'),'Занято VRAM',(FormatValue $used 'N2' ' ГБ'),'Всего VRAM',(FormatValue $total 'N2' ' ГБ'))
      }
      'AI' {
        $ui.Heading.Text='Леший';$ui.ChartName.Text='Скорость генерации токенов'
        $states=@{generating='Генерация / обработка';idle='Простой';partial='Часть счётчиков недоступна';unavailable='Счётчики недоступны'}
        $state='Нет замера';if($s -and $states.ContainsKey([string]$s.generation_state)){$state=$states[[string]$s.generation_state]}
        $ui.Subheading.Text=$state
        ShowCards @('Сейчас',(FormatValue $s.rate 'N1' ' ток/с'),'Средняя за сеанс',(FormatValue $s.rate_mean 'N1' ' ток/с'),'Пик за сеанс',(FormatValue $s.rate_peak 'N1' ' ток/с'),'Текущие токены',(FormatValue $s.tokens_now 'N0'),'Входные за журнал',(FormatValue $s.completed.input 'N0'),'Выходные за журнал',(FormatValue $s.completed.output 'N0'))
        if($s) {
            $models=@($s.models | ForEach-Object {if($_.model){$m=[string]$_.model;if($_.backend){$m+=' · '+$_.backend};$m}})
            $modelText=if($models.Count){$models -join '; '}else{'Модель / backend не подтверждены'}
            $ui.Details.Text=$modelText
            if($s.completed){$ui.Details.Text+=[Environment]::NewLine+'Завершённые записи: '+(FormatValue $s.completed.count 'N0')+' | всего токенов: '+(FormatValue $s.completed.total 'N0')+' | среднее/макс. выходных: '+(FormatValue $s.completed.mean_output 'N0')+' / '+(FormatValue $s.completed.max_output 'N0')+' | средняя/макс. скорость: '+(FormatValue $s.completed.mean_speed 'N1')+' / '+(FormatValue $s.completed.max_speed 'N1')+' ток/с'}
            $ui.Details.Text+=[Environment]::NewLine+'История — доступные хвосты журналов, не пожизненный расход. Средняя за сеанс рассчитана по интервалам с новыми токенами на слот.'
        }
      }
    }
    if($s -and $script:section -ne 'AI'){$ui.Details.Text='Системные счётчики. График строится по фактическим замерам за последние 60 секунд; разрывы означают отсутствие данных.'}
    $taskLabels=@{Running='Выполняется';Ready='Ожидает запуска';Disabled='Отключён';Queued='В очереди'}
    $ui.BuildState.Text=$(if($s -and $taskLabels.ContainsKey([string]$s.task)){$taskLabels[[string]$s.task]}elseif($s){[string]$s.task}else{'Нет замера'})
    $ui.BuildStage.Text=$(if($s -and $s.task -eq 'Running'){[string]$s.stage}else{'Последний этап: '+$(if($s){[string]$s.stage}else{'—'})})
    DrawPlot
}
function StartCollector {
    if(-not(Test-Path -LiteralPath $collector -PathType Leaf)) {throw 'Не найден компонент сбора счётчиков.'}
    $psi=[System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName=Join-Path $PSHOME 'powershell.exe'
    $psi.Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$collector+'" -Repository "'+$repo+'" -BrainRoot "'+$brain+'" -Interval '+$Interval+' -JsonLines'
    $psi.UseShellExecute=$false;$psi.CreateNoWindow=$true;$psi.RedirectStandardOutput=$true
    $psi.StandardOutputEncoding=[System.Text.UTF8Encoding]::new($false)
    $script:process=[System.Diagnostics.Process]::Start($psi)
    $script:collectorStarted=[datetime]::UtcNow
    $script:readTask=$script:process.StandardOutput.ReadLineAsync()
}
function StopCollector {
    $p=$script:process;$script:process=$null;$script:readTask=$null
    if($p){try{if(-not $p.HasExited){$p.Kill()}}catch{};try{$p.Dispose()}catch{}}
}
function AcceptSample($s) {
    if(-not $s.timestamp){return}
    try{[void][datetimeoffset]::Parse([string]$s.timestamp)}catch{return}
    $script:sample=$s;$script:lastReceived=[datetime]::UtcNow
    $script:history.Add($s)
    while($script:history.Count -gt 120){$script:history.RemoveAt(0)}
    $ids=@($s.gpus | Where-Object {$null -ne $_} | ForEach-Object {[string]$_.index})
    $existing=@($ui.GpuSelect.Items | ForEach-Object {[string]$_.Tag})
    if(($ids -join '|') -ne ($existing -join '|')){
        $script:updatingGpu=$true
        try{
            $ui.GpuSelect.Items.Clear()
            foreach($g in @($s.gpus)) {
                if($null -eq $g){continue}
                $item=[System.Windows.Controls.ComboBoxItem]::new();$item.Tag=[string]$g.index;$item.Content='GPU '+$g.index+' — '+$g.name
                [void]$ui.GpuSelect.Items.Add($item)
            }
            $index=0
            if($null -ne $script:gpuKey -and $ids -contains $script:gpuKey){$index=[array]::IndexOf($ids,$script:gpuKey)}
            $ui.GpuSelect.SelectedIndex=$(if($ids.Count){$index}else{-1})
            $script:gpuKey=$(if($ids.Count){$ids[$index]}else{$null})
        }finally{$script:updatingGpu=$false}
    }
    RenderMonitor
    $script:failure=''
}
$ui.GpuSelect.Add_SelectionChanged({if(-not $script:updatingGpu -and $ui.GpuSelect.SelectedItem){$script:gpuKey=[string]$ui.GpuSelect.SelectedItem.Tag;RenderMonitor}})
$ui.Plot.Add_SizeChanged({DrawPlot})
$ui.ConsoleButton.Add_Click({
    try{
        $arg='-NoLogo -NoProfile -ExecutionPolicy Bypass -File "'+$collector+'" -Repository "'+$repo+'" -BrainRoot "'+$brain+'"'
        Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arg
    }catch{$ui.Footer.Text='Не удалось открыть технический режим.'}
})
$timer=[System.Windows.Threading.DispatcherTimer]::new();$timer.Interval=[timespan]::FromMilliseconds(250)
$timer.Add_Tick({
    try{
        if($null -eq $script:process -and [datetime]::UtcNow -ge $script:retryAfter){StartCollector}
        if($script:process -and $script:collectorStarted){
            $latest=$script:collectorStarted
            if($script:lastReceived -and $script:lastReceived -gt $latest){$latest=$script:lastReceived}
            if(([datetime]::UtcNow-$latest).TotalSeconds -gt 45){throw 'Сборщик не отвечает.'}
        }
        $n=0
        while($script:readTask -and $script:readTask.IsCompleted -and $n -lt 10){
            $line=$script:readTask.GetAwaiter().GetResult()
            if($null -eq $line){throw 'Сборщик завершился.'}
            $script:readTask=$script:process.StandardOutput.ReadLineAsync();$n++
            if($line.Length -le 1048576 -and $line.StartsWith('{')){try{AcceptSample ($line|ConvertFrom-Json)}catch{$script:failure='Ошибка формата измерений'}}
        }
    }catch{
        StopCollector;$script:failure='Сборщик недоступен; повтор через 10 секунд';$script:retryAfter=[datetime]::UtcNow.AddSeconds(10)
    }
    $age=if($script:lastReceived){([datetime]::UtcNow-$script:lastReceived).TotalSeconds}else{[double]::PositiveInfinity}
    if($script:failure){$ui.LiveState.Text=$script:failure;$ui.LiveState.Foreground=Brush '#F4BB62'}
    elseif($age -lt 10){$ui.LiveState.Text='Мониторинг активен';$ui.LiveState.Foreground=Brush '#7CD8AB'}
    elseif($script:lastReceived){$ui.LiveState.Text='Данные устарели';$ui.LiveState.Foreground=Brush '#F4BB62'}
    else{$ui.LiveState.Text='Подключение к счётчикам…';$ui.LiveState.Foreground=Brush '#A9B6C6'}
    $ui.UpdatedAt.Text=if($script:lastReceived){'Замер: '+$script:lastReceived.ToLocalTime().ToString('HH:mm:ss')+' · '+[Math]::Round($age)+' сек. назад'}else{'Ожидается первый замер'}
})
$window.Add_Closed({$timer.Stop();StopCollector})
RenderMonitor
$timer.Start()
try{[void]$window.ShowDialog()}finally{$timer.Stop();StopCollector}
